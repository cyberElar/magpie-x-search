'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { verifyRelease } = require('./.github/scripts/verify-release.cjs');

function repository(t, pkg = { name: 'magpie-x-search', version: '1.2.3' }) {
  const cwd = mkdtempSync(join(tmpdir(), 'magpie release '));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const git = (...args) => execFileSync('git', args, {
    cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
  git('init', '--initial-branch=main');
  git('config', 'user.name', 'Release Test');
  git('config', 'user.email', 'release-test@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('config', 'tag.gpgsign', 'false');
  writeFileSync(join(cwd, 'package.json'), JSON.stringify(pkg));
  git('add', 'package.json');
  git('commit', '-m', 'Add package');
  const workflowSha = git('rev-parse', 'HEAD');
  const verify = (tag, extra = {}) => verifyRelease({
    tag, workflowRef: 'refs/heads/main', workflowSha, ...extra,
  }, cwd);
  return { cwd, git, verify, workflowSha };
}

test('release accepts a lightweight tag on the workflow main commit', (t) => {
  const repo = repository(t);
  repo.git('tag', 'v1.2.3');
  assert.equal(repo.verify('v1.2.3'), repo.workflowSha);
});

test('release accepts an annotated tag on an earlier main commit', (t) => {
  const repo = repository(t);
  repo.git('tag', '-a', 'v1.2.3', '-m', 'Release 1.2.3');
  repo.git('commit', '--allow-empty', '-m', 'Advance main');
  assert.equal(repo.verify('v1.2.3', { workflowSha: repo.git('rev-parse', 'HEAD') }), repo.workflowSha);
});

test('release rejects a branch with the release tag name', (t) => {
  const repo = repository(t);
  repo.git('branch', 'v1.2.3');
  assert.throws(() => repo.verify('v1.2.3'), /tag must exist/);
});

test('release rejects a tag on a commit outside the workflow main history', (t) => {
  const repo = repository(t);
  repo.git('switch', '-c', 'unreviewed');
  repo.git('commit', '--allow-empty', '-m', 'Unreviewed commit');
  repo.git('tag', 'v1.2.3');
  assert.throws(() => repo.verify('v1.2.3'), /must be part of/);
});

test('release rejects tags whose version differs from the package version', (t) => {
  const repo = repository(t);
  repo.git('tag', 'v1.2.4');
  assert.throws(() => repo.verify('v1.2.4'), /match the package version/);
});

test('release rejects a different npm package', (t) => {
  const repo = repository(t, { name: 'another-package', version: '1.2.3' });
  repo.git('tag', 'v1.2.3');
  assert.throws(() => repo.verify('v1.2.3'), /package name must be/);
});

test('release rejects workflows dispatched from another branch or a tag', (t) => {
  const repo = repository(t);
  repo.git('tag', 'v1.2.3');
  for (const workflowRef of ['refs/heads/feature', 'refs/tags/v1.2.3']) {
    assert.throws(() => repo.verify('v1.2.3', { workflowRef }), /from main/);
  }
});

test('release rejects revision expressions, shell input, and prerelease versions', (t) => {
  const repo = repository(t);
  for (const tag of ['main', 'v1.2.3^{commit}', 'v1.2.3;echo bad', 'v1.2.3\nbad', 'v1.2.3-rc.1', 'v01.2.3']) {
    assert.throws(() => repo.verify(tag), /stable release tag/);
  }
});

test('release rejects an unpinned workflow commit', (t) => {
  const repo = repository(t);
  assert.throws(() => repo.verify('v1.2.3', { workflowSha: 'main' }), /full Git commit SHA/);
});
