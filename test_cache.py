import concurrent.futures
from contextlib import closing
import multiprocessing
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from search_backend import RequestScope, SearchError, Settings, search_request
from search_cache import SearchCache, cache_key


def process_search(path, barrier, release, count, results):
    cache = SearchCache(path, 300)
    scope = RequestScope(8)
    try:
        barrier.wait(timeout=5)
        def fetch():
            with count.get_lock():
                count.value += 1
            while not release.is_set():
                scope.wait(0.02)
            return {"text": "shared", "retrieved_at": "original-time"}
        results.put(cache.run("same-query", "use", scope, fetch))
    except Exception as exc:
        results.put({"failure": str(exc)})
    finally:
        scope.close()


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "cache.sqlite3")
        self.cache = SearchCache(self.path, 300)
        self.calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self):
        self.calls += 1
        return {"text": str(self.calls), "retrieved_at": "original-time"}

    def run_cache(self, mode="use", scope=None, fetch=None, key="key"):
        owned = scope is None
        scope = scope or RequestScope(3)
        try:
            return self.cache.run(key, mode, scope, fetch or self.fetch)
        finally:
            if owned:
                scope.close()

    def test_hit_refresh_bypass_and_expiration(self):
        miss = self.run_cache()
        hit = self.run_cache()
        self.assertEqual((miss["cache"]["status"], hit["cache"]["status"]), ("miss", "hit"))
        self.assertEqual(hit["retrieved_at"], "original-time")
        fresh = self.run_cache("refresh")
        self.assertEqual(fresh["text"], "2")
        bypass = self.run_cache("bypass")
        self.assertEqual(bypass["cache"]["status"], "bypass")
        self.assertEqual(self.run_cache()["text"], "2")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE results SET expires=0")
        self.assertEqual(self.run_cache()["text"], "4")
        self.cache.ttl = 0
        self.assertEqual(self.run_cache()["cache"]["status"], "bypass")

    def test_different_filters_and_models_use_different_keys(self):
        base = search_request({"query": "q"}, Settings())
        key = cache_key("http://localhost", base)
        self.assertNotEqual(key, cache_key("http://127.0.0.1", base))
        self.assertNotEqual(key, cache_key("http://localhost", {**base, "model": "other"}))
        self.assertNotEqual(key, cache_key("http://localhost", search_request({"query": "q", "allowed_x_handles": ["test"]}, Settings())))
        self.assertEqual(key, cache_key("http://localhost", search_request({"query": "q", "cache_mode": "refresh"}, Settings())))

    def test_expired_owner_is_replaced(self):
        self.cache.initialize()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO leases VALUES ('key','dead-process',0)")
        self.assertEqual(self.run_cache()["cache"]["status"], "miss")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM leases").fetchone()[0], 0)

    def test_live_owner_renews_lease(self):
        self.cache.lease_seconds = 1
        started, renewed, release = threading.Event(), threading.Event(), threading.Event()
        initial_expiry = []
        original_renew = self.cache.renew

        def renew(key, owner):
            changed = original_renew(key, owner)
            if changed and initial_expiry and time.time() > initial_expiry[0]:
                renewed.set()
            return changed

        self.cache.renew = renew

        def fetch():
            with closing(sqlite3.connect(self.path)) as db, db:
                initial_expiry.append(db.execute("SELECT expires FROM leases WHERE key='key'").fetchone()[0])
            started.set()
            self.assertTrue(release.wait(6))
            return self.fetch()

        scope = RequestScope(8)
        try:
            with concurrent.futures.ThreadPoolExecutor(1) as executor:
                future = executor.submit(self.run_cache, scope=scope, fetch=fetch)
                try:
                    self.assertTrue(started.wait(2))
                    # Observe a committed renewal after the first lease expired.
                    self.assertTrue(renewed.wait(4), "Owner did not renew beyond its original lease")
                    with closing(sqlite3.connect(self.path)) as db, db:
                        expires = db.execute("SELECT expires FROM leases WHERE key='key'").fetchone()[0]
                    self.assertGreater(expires, initial_expiry[0])
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=2)["text"], "1")
        finally:
            scope.close()

    def test_live_owner_is_not_replaced_when_waiter_times_out(self):
        self.cache.initialize()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO leases VALUES ('key','live-process',?)", (time.time()+30,))
        scope = RequestScope(0.1)
        try:
            with self.assertRaises(SearchError) as caught:
                self.run_cache(scope=scope)
            self.assertEqual(caught.exception.code, "timeout")
            self.assertEqual(self.calls, 0)
        finally:
            scope.close()

    def test_cancelled_follower_does_not_cancel_leader(self):
        started, release, waiting = threading.Event(), threading.Event(), threading.Event()
        original_claim = self.cache.claim
        def claim(*args):
            result = original_claim(*args)
            if result[0] == "wait":
                waiting.set()
            return result
        self.cache.claim = claim
        def fetch():
            started.set()
            self.assertTrue(release.wait(2))
            return self.fetch()
        scope = RequestScope(3)
        try:
            with concurrent.futures.ThreadPoolExecutor(2) as executor:
                leader = executor.submit(self.run_cache, fetch=fetch)
                self.assertTrue(started.wait(1))
                follower = executor.submit(self.run_cache, scope=scope)
                self.assertTrue(waiting.wait(1))
                scope.stop()
                with self.assertRaises(SearchError) as caught:
                    follower.result(timeout=1)
                self.assertEqual(caught.exception.code, "cancelled")
                release.set()
                self.assertEqual(leader.result(timeout=1)["text"], "1")
        finally:
            release.set()
            scope.close()
        self.assertEqual(self.run_cache()["cache"]["status"], "hit")
        self.assertEqual(self.calls, 1)

    def test_coalesced_failure_does_not_retry_upstream(self):
        started, release, waiting = threading.Event(), threading.Event(), threading.Event()
        original_claim = self.cache.claim
        def claim(*args):
            result = original_claim(*args)
            if result[0] == "wait":
                waiting.set()
            return result
        self.cache.claim = claim
        def fail():
            self.calls += 1
            started.set()
            self.assertTrue(release.wait(2))
            raise SearchError("rate_limited", "limited", retry_after_sec=7)
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            leader = executor.submit(self.run_cache, fetch=fail)
            self.assertTrue(started.wait(1))
            follower = executor.submit(self.run_cache, fetch=fail)
            self.assertTrue(waiting.wait(1))
            release.set()
            for future in (leader, follower):
                with self.assertRaises(SearchError) as caught:
                    future.result(timeout=1)
                self.assertEqual(caught.exception.details["retry_after_sec"], 7)
        self.assertEqual(self.calls, 1)
        # An explicit new request can try again; an old failure is not a cache hit.
        self.assertEqual(self.run_cache()["text"], "2")

    def test_cross_process_coalescing(self):
        context = multiprocessing.get_context("spawn")
        barrier, release = context.Barrier(4), context.Event()
        count, results = context.Value('i', 0), context.Queue()
        processes = [context.Process(target=process_search, args=(self.path, barrier, release, count, results)) for _ in range(3)]
        for process in processes:
            process.start()
        try:
            barrier.wait(timeout=5)
            deadline = time.monotonic()+3
            while count.value == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(count.value, 1)
            release.set()
            values = [results.get(timeout=5) for _ in processes]
            self.assertEqual(count.value, 1)
            self.assertTrue(all(v.get("text") == "shared" for v in values), values)
            self.assertEqual(sum(v["cache"]["status"] == "miss" for v in values), 1)
        finally:
            release.set()
            for process in processes:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join()
            results.close()
            results.join_thread()

    def test_broken_cache_has_explicit_error_and_bypass_works(self):
        Path(self.path).write_text("not a database")
        with self.assertRaises(SearchError) as caught:
            self.run_cache()
        self.assertEqual(caught.exception.code, "cache_error")
        self.assertEqual(self.run_cache("bypass")["text"], "1")


if __name__ == "__main__":
    unittest.main()
