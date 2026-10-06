'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const pkg = require('./package.json');
const read = (file) => readFileSync(join(__dirname, file), 'utf8');
const pythonVersion = read('x_search_mcp.py').match(/^VERSION = "([^"]+)"$/m)?.[1];
assert.equal(pkg.version, pythonVersion, 'npm and MCP versions must match');
assert.equal(pkg.license, 'MIT');
assert.ok(read('LICENSE').startsWith('MIT License\n'));
assert.ok(read('launcher.cjs').startsWith('#!/usr/bin/env node\n'));
for (const file of pkg.files) read(file);
console.error(`Verified magpie-x-search ${pkg.version} package sources.`);
