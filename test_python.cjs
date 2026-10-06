'use strict';

const { spawnSync } = require('node:child_process');
const { findPython } = require('./launcher.cjs');
try {
  const python = findPython();
  const result = spawnSync(python.command, [...python.args, '-m', 'unittest', 'discover', '-v'], {
    cwd: __dirname, stdio: 'inherit', env: { ...process.env, PYTHONUTF8: '1' },
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
