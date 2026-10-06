'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { mkdtempSync, mkdirSync, rmSync } = require('node:fs');
const { createServer } = require('node:http');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { createInterface } = require('node:readline');
const { once } = require('node:events');
const pkg = require('./package.json');

function npm(args, cwd, cache) {
  assert.ok(process.env.npm_execpath, 'Run package tests with npm test');
  const result = spawnSync(process.execPath, [process.env.npm_execpath, '--cache', cache, ...args], {
    cwd, encoding: 'utf8', timeout: 60000,
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return result.stdout;
}

function client(child) {
  const pending = new Map();
  let stderr = '';
  child.stderr.on('data', (data) => { stderr += data; });
  const lines = createInterface({ input: child.stdout });
  lines.on('line', (line) => {
    const reply = JSON.parse(line);
    pending.get(reply.id)?.resolve(reply);
    pending.delete(reply.id);
  });
  const ended = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code, signal) => {
      for (const waiter of pending.values()) waiter.reject(new Error(stderr || 'MCP exited before replying'));
      resolve({ code, signal, stderr });
    });
  });
  return {
    ended,
    send(value) { child.stdin.write(JSON.stringify(value) + '\n'); },
    call(value) {
      return new Promise((resolve, reject) => {
        pending.set(value.id, { resolve, reject });
        this.send(value);
      });
    },
  };
}

test('packed package installs without scripts and serves MCP from a directory with spaces', { timeout: 90000 }, async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'magpie package '));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const cache = join(dir, 'npm-cache');
  const report = JSON.parse(npm(['pack', '--json', '--pack-destination', dir], __dirname, cache));
  const packed = Array.isArray(report) ? report[0] : report[pkg.name];
  assert.deepEqual(packed.files.map((file) => file.path).sort(), [
    'LICENSE', 'README.md', 'launcher.cjs', 'package.json', 'search_backend.py', 'search_cache.py', 'x_search_mcp.py',
  ].sort());
  assert.equal(packed.name, pkg.name);
  assert.equal(packed.version, pkg.version);
  const prefix = join(dir, 'global install');
  mkdirSync(prefix);
  npm(['install', '--global', '--prefix', prefix, '--ignore-scripts', '--no-audit', '--no-fund', '--offline',
    join(dir, packed.filename)], dir, cache);
  const launcher = process.platform === 'win32'
    ? join(prefix, 'node_modules', pkg.name, 'launcher.cjs')
    : join(prefix, 'bin', pkg.name);
  const version = spawnSync(process.execPath, [launcher, '--version'], { encoding: 'utf8' });
  assert.equal(version.status, 0, version.stderr);
  assert.equal(version.stdout.trim(), pkg.version);
  const npxVersion = npm(['exec', '--yes', '--offline', `--package=${join(dir, packed.filename)}`,
    '--', pkg.name, '--version'], dir, cache);
  assert.equal(npxVersion.trim(), pkg.version);

  let request;
  const server = createServer((req, res) => {
    let body = '';
    req.on('data', (data) => { body += data; });
    req.on('end', () => {
      request = JSON.parse(body);
      const url = 'https://x.com/example/status/123';
      const response = { status: 'completed', model: 'offline-grok',
        usage: { server_side_tool_usage_details: { x_search_calls: 1, x_posts_fetched: 1 } },
        output: [{ type: 'message', content: [{ type: 'output_text',
          text: JSON.stringify({ text: '中文结果', posts: [{ url, text: '公开帖子', author: 'example',
            created_at: null, kind: 'original' }] }), annotations: [{ type: 'url_citation', url }] }] }] };
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.end(`data: ${JSON.stringify({ type: 'response.completed', response })}\n\n`);
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const env = { ...process.env, X_SEARCH_MAGPIE_URL: `http://127.0.0.1:${server.address().port}/v1/responses`,
    X_SEARCH_CACHE_TTL_SEC: '0', X_SEARCH_TIMEOUT_SEC: '5' };
  delete env.X_SEARCH_MAX_OUTPUT_TOKENS;
  const child = spawn(process.execPath, [launcher], { cwd: dir, env, stdio: 'pipe' });
  const mcp = client(child);
  t.after(async () => { child.stdin.end(); child.kill(); await mcp.ended; });
  const init = await mcp.call({ jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-11-25' } });
  assert.equal(init.result.serverInfo.version, pkg.version);
  mcp.send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  const result = await mcp.call({ jsonrpc: '2.0', id: 2, method: 'tools/call',
    params: { name: 'x_search', arguments: { query: '公开帖子', cache_mode: 'bypass' } } });
  assert.equal(result.result.isError, false);
  assert.equal(result.result.structuredContent.text, '中文结果');
  assert.equal(result.result.structuredContent.posts.length, 1);
  assert.equal(request.input[0].content, '公开帖子');
  assert.ok(!Object.hasOwn(request, 'max_output_tokens'));
  child.stdin.end();
  assert.equal((await mcp.ended).code, 0);

  if (process.platform !== 'win32') {
    const running = spawn(process.execPath, [launcher], { cwd: dir, env, stdio: 'pipe' });
    const interrupted = client(running);
    t.after(async () => { running.stdin.end(); running.kill(); await interrupted.ended; });
    await interrupted.call({ jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2025-11-25' } });
    running.kill('SIGTERM');
    assert.equal((await interrupted.ended).code, 143);
  }
});
