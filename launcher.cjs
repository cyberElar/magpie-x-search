#!/usr/bin/env node
'use strict';

const { spawn, spawnSync } = require('node:child_process');
const { join } = require('node:path');
const { constants } = require('node:os');
const { version } = require('./package.json');

const PYTHON_CHECK = 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)';

function findPython({ env = process.env, platform = process.platform, probe = spawnSync } = {}) {
  const candidates = env.X_SEARCH_PYTHON
    ? [{ command: env.X_SEARCH_PYTHON, args: [] }]
    : platform === 'win32'
      ? [{ command: 'py', args: ['-3'] }, { command: 'python', args: [] }, { command: 'python3', args: [] }]
      : [{ command: 'python3', args: [] }, { command: 'python', args: [] }];
  for (const candidate of candidates) {
    const result = probe(candidate.command, [...candidate.args, '-c', PYTHON_CHECK], {
      env, stdio: 'ignore', timeout: 5000, windowsHide: true,
    });
    if (!result.error && result.status === 0) return candidate;
  }
  throw new Error(env.X_SEARCH_PYTHON
    ? 'X_SEARCH_PYTHON must name a working Python 3.10+ executable, without command arguments.'
    : 'Python 3.10+ is required. Install Python or set X_SEARCH_PYTHON to its executable path.');
}

function main(args = process.argv.slice(2)) {
  if (args.length === 1 && args[0] === '--version') {
    console.log(version);
    return;
  }
  if (args.length === 1 && args[0] === '--help') {
    console.log(`magpie-x-search ${version}

Usage: magpie-x-search [--help | --version]

Starts an MCP stdio server. Requires Python 3.10+ and a local magpie Grok login.
Set X_SEARCH_PYTHON to a Python executable path to override automatic detection.
See https://github.com/cyberElar/magpie-x-search#configuration for search settings.`);
    return;
  }
  if (args.length) {
    console.error('magpie-x-search: unsupported arguments. Use --help for usage.');
    process.exitCode = 2;
    return;
  }
  let python;
  try {
    python = findPython();
  } catch (error) {
    console.error(`magpie-x-search: ${error.message}`);
    process.exitCode = 1;
    return;
  }
  const child = spawn(python.command, [...python.args, '-u', join(__dirname, 'x_search_mcp.py')], {
    stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1' }, windowsHide: true,
  });
  const forward = (signal) => child.kill(signal);
  const interrupt = () => forward('SIGINT');
  const terminate = () => forward('SIGTERM');
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  child.once('error', () => {
    console.error('magpie-x-search: cannot start Python. Check X_SEARCH_PYTHON and executable permissions.');
  });
  child.once('close', (code, signal) => {
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', terminate);
    process.exitCode = code ?? (signal ? 128 + (constants.signals[signal] || 1) : 1);
  });
}

module.exports = { findPython, main };
if (require.main === module) main();
