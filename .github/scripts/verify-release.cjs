'use strict';

const { execFileSync } = require('node:child_process');
const { appendFileSync } = require('node:fs');

function verifyRelease({ tag, workflowRef, workflowSha }, cwd = process.cwd()) {
  if (workflowRef !== 'refs/heads/main') {
    throw new Error('Run the release workflow from main.');
  }
  if (!/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(tag || '')) {
    throw new Error('Use a stable release tag in the form vMAJOR.MINOR.PATCH.');
  }
  if (!/^[a-f0-9]{40}$/.test(workflowSha || '')) {
    throw new Error('The workflow commit must be a full Git commit SHA.');
  }
  const git = (...args) => execFileSync('git', args, {
    cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();

  let releaseSha;
  try {
    releaseSha = git('rev-parse', '--verify', `refs/tags/${tag}^{commit}`);
  } catch {
    throw new Error('The release tag must exist and point to a commit.');
  }
  try {
    git('merge-base', '--is-ancestor', releaseSha, workflowSha);
  } catch {
    throw new Error('The release commit must be part of the workflow main commit.');
  }
  const pkg = JSON.parse(git('show', `${releaseSha}:package.json`));
  if (pkg.name !== 'magpie-x-search') {
    throw new Error('The release package name must be magpie-x-search.');
  }
  if (tag !== `v${pkg.version}`) {
    throw new Error('The release tag must match the package version.');
  }
  return releaseSha;
}

if (require.main === module) {
  try {
    const releaseSha = verifyRelease({
      tag: process.env.RELEASE_TAG,
      workflowRef: process.env.GITHUB_REF,
      workflowSha: process.env.GITHUB_SHA,
    });
    appendFileSync(process.env.GITHUB_OUTPUT, `release-sha=${releaseSha}\n`);
    console.log(`Verified ${process.env.RELEASE_TAG} at ${releaseSha}.`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

module.exports = { verifyRelease };
