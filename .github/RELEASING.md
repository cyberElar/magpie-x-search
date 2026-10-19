# Release controls

The release workflow stages stable npm packages. A maintainer approves each
staged package on npm before the version becomes public.

## Repository settings

- Protect `main` with a ruleset that requires a pull request and all test checks.
- Require zero approving PR reviews while the repository has one maintainer.
- Block force pushes and deletion of `main`. Do not add bypass actors.
- Restrict creation of `v*` tags to the release maintainer.
- Block updates and deletion of `v*` tags in a separate ruleset without bypass actors.
- Configure the `npm-publish` environment to allow only the `main` branch.
- Require approval from `cyberElar` for that environment. Allow self-review.
- Disable administrator bypass for the environment.

All workflow actions use full commit SHAs. Dependabot checks the action versions
each week.

## npm settings

Configure the trusted publisher for `magpie-x-search` with these values:

| Field | Value |
| --- | --- |
| Provider | GitHub Actions |
| Repository | `cyberElar/magpie-x-search` |
| Workflow file | `publish.yml` |
| Environment | `npm-publish` |
| Allow `npm stage publish` | Yes |
| Allow `npm publish` | No |
| Allow `npm dist-tag` | No |

Remove the previous publisher that does not require this environment.
Otherwise, that publisher still permits jobs to bypass the environment.
Select **Require two-factor authentication and disallow tokens** under
the npm package's publishing access settings.

## Stage and approve a release

Do not move or delete a release tag. Existing tags are immutable.

1. Merge the version change through a pull request after all tests pass.
2. Create a `vMAJOR.MINOR.PATCH` tag on the release commit in `main`.
3. Push the tag to the repository.
4. Run `publish.yml` from `main` with the tag as the `tag` input.
5. Check the release commit and artifact digest in the build job summary.
6. Approve the `npm-publish` environment to stage the package on npm.
7. Inspect the staged version and package contents on npm.
8. Approve the staged package on npm with 2FA.

The build job checks the tag's name, package name, version, and ancestry against
the workflow's `main` commit. The build job then checks out the resolved commit,
runs the tests, and uploads the package.

The publish job has OIDC permission. It downloads the artifact from the build job,
checks its SHA-256 checksum, and stages the tarball without running package scripts.
The publish job does not check out repository source.

You can approve a staged package through the npm website or the CLI:

```sh
npm stage list magpie-x-search
npm stage view <stage-id>
npm stage download <stage-id>
npm stage approve <stage-id>
```
