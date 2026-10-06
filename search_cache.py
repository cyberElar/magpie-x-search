"""Share completed searches and in-flight work across local MCP processes."""
import contextlib
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from search_backend import SearchError, search_request


def cache_key(endpoint, body):
    value = json.dumps({"version": 2, "endpoint": endpoint, "request": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


class SearchCache:
    def __init__(self, path, ttl, lease_seconds=15):
        self.path, self.ttl, self.lease_seconds = Path(path), ttl, lease_seconds
        self.initialized = False
        self.init_lock = threading.Lock()

    @contextlib.contextmanager
    def connect(self):
        with contextlib.closing(sqlite3.connect(str(self.path), timeout=0.1)) as db:
            yield db

    def initialize(self):
        with self.init_lock:
            if self.initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                           "is_error INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)")
                db.commit()
            self.initialized = True

    def claim(self, key, owner, started, waited, mode):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value,is_error,created,expires FROM results WHERE key=?", (key,)).fetchone()
            if row and row[3] > now:
                value, is_error, created, _ = row
                eligible = (waited and created >= started) if is_error else (mode == "use" or created >= started)
                if eligible:
                    db.commit()
                    return "result", (json.loads(value), is_error, max(0, now - created))
            db.execute("DELETE FROM leases WHERE key=? AND expires<=?", (key, now))
            changed = db.execute("INSERT OR IGNORE INTO leases VALUES (?,?,?)", (key, owner, now + self.lease_seconds)).rowcount
            db.commit()
            return ("leader" if changed else "wait"), None

    def renew(self, key, owner):
        with self.connect() as db:
            changed = db.execute("UPDATE leases SET expires=? WHERE key=? AND owner=?",
                                 (time.time() + self.lease_seconds, key, owner)).rowcount
            db.commit()
            return bool(changed)

    def save(self, key, owner, value, is_error=False):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lease = db.execute("SELECT owner FROM leases WHERE key=?", (key,)).fetchone()
            if not lease or lease[0] != owner:
                db.rollback()
                raise SearchError("cache_lease", "Shared search ownership was lost; please call again")
            ttl = 5 if is_error else self.ttl
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?)",
                       (key, json.dumps(value, ensure_ascii=False), int(is_error), now, now + ttl))
            db.execute("DELETE FROM results WHERE expires<=?", (now,))
            db.execute("DELETE FROM results WHERE key NOT IN (SELECT key FROM results ORDER BY created DESC LIMIT 1000)")
            db.commit()

    def release(self, key, owner):
        with self.connect() as db:
            db.execute("DELETE FROM leases WHERE key=? AND owner=?", (key, owner))
            db.commit()

    def lead(self, key, owner, scope, fetch):
        stopped = threading.Event()

        def keep_lease():
            while not stopped.wait(self.lease_seconds / 3):
                try:
                    if not self.renew(key, owner):
                        scope.stop("cache_lease")
                        return
                except sqlite3.Error:
                    # A failed renewal must not leave two upstream leaders running.
                    scope.stop("cache_lease")
                    return

        heartbeat = threading.Thread(target=keep_lease, daemon=True)
        heartbeat.start()
        try:
            try:
                value = fetch()
                scope.check()
            except SearchError as exc:
                if exc.code not in ("cancelled", "timeout", "cache_lease"):
                    self.save(key, owner, {"code": exc.code, "message": str(exc), **exc.details}, is_error=True)
                raise
            self.save(key, owner, value)
            return {**value, "cache": {"status": "miss", "age_seconds": 0}}
        finally:
            stopped.set()
            heartbeat.join()
            self.release(key, owner)

    def run(self, key, mode, scope, fetch):
        scope.check()
        if mode == "bypass" or self.ttl == 0:
            value = fetch()
            scope.check()
            return {**value, "cache": {"status": "bypass", "age_seconds": 0}}
        try:
            while True:
                try:
                    self.initialize()
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    scope.wait(0.05)
            owner, started, waited = uuid.uuid4().hex, time.time(), False
            while True:
                scope.check()
                try:
                    status, result = self.claim(key, owner, started, waited, mode)
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    scope.wait(0.05)
                    continue
                if status == "result":
                    value, is_error, age = result
                    if is_error:
                        details = {k: v for k, v in value.items() if k not in ("code", "message")}
                        raise SearchError(value["code"], value["message"], **details)
                    scope.check()
                    return {**value, "cache": {"status": "coalesced" if waited else "hit", "age_seconds": round(age, 3)}}
                if status == "leader":
                    return self.lead(key, owner, scope, fetch)
                waited = True
                scope.wait(0.05)
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
            raise SearchError("cache_error", "Shared cache is unavailable; use cache_mode=bypass or fix X_SEARCH_CACHE_PATH") from exc


class SearchService:
    """The protocol calls this service; transport and cache stay independent."""
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.cache = SearchCache(settings.cache_path, settings.cache_ttl)

    def search(self, args, scope):
        body = search_request(args, self.settings)
        key = cache_key(self.settings.endpoint, body)
        return self.cache.run(key, args.get("cache_mode", "use"), scope, lambda: self.client.search(body, scope))
