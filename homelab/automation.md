# Upstream upgrades

Gitea `Nichols-HomeLab/open-terminal` is authoritative. The GitHub repository is
a true fork of `open-webui/open-terminal` and receives the Gitea push mirror.
GitHub Actions stays disabled. Gitea uses `.gitea/workflows` before the inherited
`.github/workflows`, so upstream publishing workflows remain intact but inactive.
Never create upstream-style `vX.Y.Z` tags on local commits; upstream owns those
tags. New upstream tags are fetched into an isolated local namespace and pushed
additively and atomically to Gitea, then verified on GitHub. A conflicting tag
stops automation without overwriting either copy. Images use immutable
`<upstream-version>-homelab-sha-<full-source-commit>` tags plus registry digests.
The version comes from the candidate's `pyproject.toml`, parsed with `tomllib`.
The three-part release is normalized without losing prerelease information:
`0.15.0rc1` becomes `0.15.0-rc1`, `.dev1` stays `.dev1`, and `+abc` becomes `_abc`.
Unsupported version spellings stop the run instead of inventing a stable release.
Tags must fit Docker's 128-character limit. For example, stable 0.14.0 produces
`0.14.0-homelab-sha-<40-character-commit>@sha256:<64-character-digest>`.

`homelab-upgrade.yml` runs daily at 09:23 UTC or on manual dispatch. One upgrade
runs at a time. The job has a 150-minute limit; builds, smoke tests, pushes,
network requests and mirror polling have additional finite limits.

## Integration and validation

`upstream-sync.py` fetches official upstream `main` without overwriting tags and
merges it into an isolated worktree based on current Gitea `main`. It publishes
an integration branch and PR before building. Every version, including major
versions, can merge automatically when the custom image builds and
`python3 homelab/smoke.py IMAGE` succeeds. The smoke suite checks the actual
installed application, dependency consistency, required tools, authentication,
command execution, and a persistent workspace across container replacements.

Every PR targeting main also runs the independent `Homelab fork validation`
workflow, including maintenance PRs. It checks out the exact PR head and runs
coordinator tests, an image build and the runtime smoke suite. The job receives
no repository secrets, does not publish images, and has an 80-minute deadline.
Automatic merging is restricted to candidates within the authoritative Gitea
repository; an external fork PR is never merged by the coordinator.

After observing the first real run, protect main with this exact native Actions
status context:

```
Homelab fork validation / image-smoke (pull_request)
```

The coordinator waits at most 45 minutes for that context on the exact candidate
SHA. Missing, failed, cancelled and skipped checks do not permit a merge. Its
manually posted `homelab/image-smoke` status is supplemental evidence and does
not substitute for the PR check. Scheduled and manually dispatched workflows
do not natively post commit statuses, so neither is suitable as a required
branch-protection context. A no-upstream-change recovery run merges no fork PR
and therefore needs no new PR status on the existing main commit.

Automated integration PRs deliberately build twice: once in credential-free
PR validation, once in the coordinator that retains the tested local image for
publication. The jobs can run concurrently. This avoids trusting an unverified
image rebuilt after a remote test, or introducing an image-artifact transport
just to remove the duplicate build.

Merging uses `fast-forward-only`, the tested head SHA and a check that main has
not advanced. A changed base or PR head stops the run; the next invocation
starts from the new main. No force push or automatic conflict resolution occurs.
Failed branches and PRs remain available. Conflict candidates retain the trusted
`.gitea` directory so opening a PR cannot accidentally activate upstream release
or PyPI workflows. Upstream changes to local automation policy require review.

The registry login happens after tests, using a private temporary Docker config.
Candidate subprocesses do not inherit forge or registry tokens. The tested image
is published to `git.nicholstech.org/nichols-homelab/open-terminal`; its registry
manifest digest is read back before any deployment update.

## GitOps deployment

The updater reads the current Gitea `k3s-fluxcd` main and changes only:

- `clusters/random/open-terminal/deployment.yaml`: the `open-terminal` image.
- `clusters/random/open-terminal/github-bundle-backup.yaml`: the `backup` image.

Both edits share one API commit. YAML is parsed before and after editing and
compared to prove no other field changed. The new branch is pinned to the
inspected base commit; each file operation includes its original blob SHA.
Before creating that branch, the coordinator fetches the owning repository's
`scripts/validate-manifests.py` at the inspected main commit and executes its
image checks on both candidate documents in a temporary directory with a clean
environment. No image-version exceptions are supplied. This invokes the actual
current GitOps image policy; the full manifest validator additionally renders
the complete Flux graph and remains part of the owning repository's CI.
The committed files are fetched and compared with the validated content. A PR
then merges using the exact same base/head and fast-forward guards.

Gitea's automatic mirrors are given up to 60 seconds each to put the exact fork
and GitOps main SHAs on GitHub. If main advances concurrently, GitHub's comparison
API must prove the expected commit remains an ancestor of the new main. A failed backup check leaves successful Gitea
changes intact and reports the failure. A subsequent run also checks deployment
references when upstream is already merged, so an interrupted publication or
GitOps update is retried rather than forgotten. Even a no-change run checks both
main backups and upstream tags, so previous mirror failures cannot disappear
behind a deployment-already-current result.

CI does not have Kubernetes credentials. Its success means the tested image,
Gitea commits, GitHub backups and GitOps update are verified. It does not claim
that Flux or the live workload has converged. The separate deployment audit
must check the observed Git revision, image digest and real terminal HTTP/API
health. Pod Ready alone does not prove an application is listening.

## Credentials and notifications

Configure these Gitea repository settings before enabling Actions:

- Secret `HOMELAB_AUTOMATION_TOKEN`, exposed only to the coordinator as
  `GITEA_AUTOMATION_TOKEN`. Its identity needs repository/PR/status access for
  this fork and `k3s-fluxcd`. Gitea reserves the `GITEA_` secret-name prefix.
- Secret `REGISTRY_TOKEN`, authorized to publish the Open Terminal package.
- Secret `GH_BACKUP_TOKEN`, authorized to read both GitHub backups, including
  private `k3s-fluxcd`. Public anonymous API calls cannot verify private refs.
- Variable `REGISTRY_USERNAME`, matching that registry token's account.
  The workflow also uses this service identity as `GITEA_USERNAME` for Git HTTP
  authentication; split these variables if the two credentials gain different owners.
- Allow fast-forward-only PR merging on both repositories. Configure required
  status check above on the fork; the coordinator never forces a blocked merge.

No terminal service-account token, Talos configuration, SSH private key, or
production terminal API key is supplied to CI. Credentials are never embedded in
remote URLs, build arguments, candidate environments, or notification bodies.
Git transport defaults to the configured internal Gitea API server, with a
repository-scoped HTTP authorization header supplied only to Git subprocesses.
`GITEA_GIT_URL` can override that repository URL when an approved route changes.

The authorized internal ntfy endpoint is
`http://ntfy.notifications.svc.cluster.local/updates`. Completed GitOps updates
send a notification that explicitly says live convergence is unverified.
Conflicts, failed tests, missing credentials, stale backup refs, merge races and
publication/deployment failures send the stage and retained PR links. Notification
delivery itself retries at most three times and reports failure honestly.

## Local validation

Run the coordinator safeguards without credentials:

```sh
python3 -m unittest discover -s homelab/tests -p 'test_*.py'
```

The tests cover narrowly scoped multi-document YAML edits, file-SHA guards,
atomic deployment updates, stale-base/head rejection, finite backup polling,
credential filtering, and safe inherited-workflow handling on conflict branches.
