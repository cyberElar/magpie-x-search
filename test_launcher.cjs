'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { join } = require('node:path');
const { findPython } = require('./launcher.cjs');
const { version } = require('./package.json');

function run(args, env = process.env, input = '') {
  return spawnSync(process.execPath, [join(__dirname, 'launcher.cjs'), ...args], {
    env, input, encoding: 'utf8', timeout: 15000,
  });
}

test('help and version work without Python and unsupported arguments fail', () => {
  const env = { ...process.env, X_SEARCH_PYTHON: join(__dirname, 'missing-python') };
  assert.equal(run(['--version'], env).stdout.trim(), version);
  assert.equal(run(['--help'], env).status, 0);
  const invalid = run(['--invalid'], env);
  assert.equal(invalid.status, 2);
  assert.equal(invalid.stdout, '');
});

test('missing Python reports an error only on stderr', () => {
  const result = run([], { ...process.env, X_SEARCH_PYTHON: join(__dirname, 'missing-python') });
  assert.equal(result.status, 1);
  assert.equal(result.stdout, '');
  assert.match(result.stderr, /Python 3\.10\+/);
});

test('Python detection skips incompatible versions and supports the Windows launcher', () => {
  const tried = [];
  const result = findPython({ env: {}, platform: 'win32', probe(command, args) {
    tried.push([command, args]);
    return { status: command === 'python' ? 0 : 1 };
  } });
  assert.equal(result.command, 'python');
  assert.deepEqual(tried.map(([command]) => command), ['py', 'python']);
  assert.equal(tried[0][1][0], '-3');
  assert.equal(tried[0][1][1], '-c');
});

test('explicit Python paths with spaces stay one argument and disable fallback', () => {
  const command = '/directory with spaces/python';
  let calls = 0;
  assert.throws(() => findPython({ env: { X_SEARCH_PYTHON: command }, probe(actual) {
    calls++;
    assert.equal(actual, command);
    return { status: 1 };
  } }), /X_SEARCH_PYTHON/);
  assert.equal(calls, 1);
});

test('launcher preserves MCP stdout, UTF-8, and backend error exit codes', () => {
  const env = { ...process.env, X_SEARCH_CACHE_TTL_SEC: '0' };
  const input = [
    { jsonrpc: '2.0', id: '中文', method: 'initialize', params: { protocolVersion: '2025-11-25' } },
    { jsonrpc: '2.0', method: 'notifications/initialized' },
    { jsonrpc: '2.0', id: 2, method: 'tools/list' },
  ].map(JSON.stringify).join('\n') + '\n';
  const result = run([], env, input);
  assert.equal(result.status, 0, result.stderr);
  const replies = result.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(replies.length, 2);
  assert.equal(replies[0].id, '中文');
  assert.equal(replies[0].result.serverInfo.version, version);
  assert.equal(replies[1].result.tools[0].name, 'x_search');
  const invalid = run([], { ...env, X_SEARCH_WORKERS: '0' });
  assert.equal(invalid.status, 1);
  assert.equal(invalid.stdout, '');
  assert.match(invalid.stderr, /X_SEARCH_WORKERS/);
});
