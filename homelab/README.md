# Homelab Open Terminal overlay

This directory customizes the upstream Open Terminal checkout for the Nichols
homelab. Upstream application files, packaging metadata, Dockerfiles and workflows
remain unchanged, so upstream merges retain normal Git ancestry.

## Build and validate

Build from the repository root:

```bash
docker build --build-arg SOURCE_REVISION="$(git rev-parse HEAD)" \
  -f homelab/Dockerfile -t open-terminal-homelab:test .
python3 homelab/smoke.py open-terminal-homelab:test
```

The Dockerfile runs `pip install --upgrade .` against the copied upstream source,
checks the installed distribution against `pyproject.toml`, and runs `pip check`.
An upstream version bump therefore changes the actual running Python package.
The image records the checked version in `/opt/open-terminal-source-version` and
the build commit in its OCI revision label. It does not install Open Terminal
from a PyPI version string or continue running the old base-image package.

`smoke.py` requires Python 3.11+ and Docker CLI access to a build/CI daemon. It
uses synthetic credentials, an isolated named home volume and network-disabled
containers. No cluster credentials, production Secrets or host Docker socket
mounts are needed inside the tested image. It verifies:

- the installed application version/import location and dependency consistency;
- all required CLI executables;
- startup as UID 0 with every Linux capability dropped and no-new-privileges;
- rejection of missing/incorrect API keys and successful authenticated execution;
- projected-token-file kubeconfig and private tea configuration;
- a workspace file surviving container removal and replacement on the same volume.

Containers and the temporary volume are removed on success or failure. The test
uses `docker cp` for fixtures, so it also works with the homelab's per-job
Docker-in-Docker daemon without assuming CI host paths are bind-mountable.

## Pinned toolchain provenance

The base is the previously deployed and validated image:

```
git.nicholstech.org/nichols-homelab/open-terminal:0.11.35-homelab-20261001@sha256:e543124b217105d8d629fd334090791cb1b3740575e87384e4d5afd3f5843857
```

Its source is `Nichols-HomeLab/k3s-fluxcd/images/open-terminal`. That image added
SOPS, GitHub CLI and the homelab runtime to the earlier locked HomeLab toolchain
(`Nichols-HomeLab/HomeLab`, `agent/lab-work`, commit `31ddff5`, base digest
`sha256:8ef463b8dd50a84bbc4b4ad7762cbcbc0e7df0ef9e48ede1e68c95f27a56012e`).
Retaining this immutable base avoids repeatedly downloading the working .NET,
Node and infrastructure CLI toolchain during the fork migration. Builds require
the published base image, but do not clone or build either predecessor repo.

`tools.lock.json` records the inherited binaries and checksums, and
`install-tools.py` is retained for deliberate toolchain refreshes or a future
independent Python/Debian rebase. Run it at image-build time after placing the
lock at `/opt/homelab/tools.lock.json`; without arguments it installs all entries.
The ordinary build reuses the validated installed tools. It performs no runtime
package installation. A future base refresh should preserve these versions or
update the lock deliberately, supply .NET's native dependencies, rebuild, and
pass the smoke test before updating GitOps.

## Kubernetes runtime

The existing `random/open-terminal` workload remains the deployment owner. Keep
its persistent `/home/user` mount, optional `/mnt/cephfs`, `openwebui-agent`
ServiceAccount, projected service-account token/CA, and Secret mounts. Publish
the new image to Gitea's registry and update the digest-pinned GitOps image
reference; verify Flux and workload health after reconciliation.

The startup script creates `/home/user/workspace` and `/home/user/worktrees`,
writes a kubeconfig referencing Kubernetes' rotating `tokenFile`, configures git
and tea from mounted Secrets, and starts the installed application through normal
Python/Uvicorn. It deliberately avoids the inherited capability-bearing console
wrapper, which is incompatible with the workload's dropped capabilities.

Required runtime inputs are `OPEN_TERMINAL_API_KEY`, Kubernetes service host/port,
and `/run/secrets/homelab/{github-token,gitea-token,identity,known_hosts}`. Existing
host SSH and Talos access additionally use `host-identity` and `talosconfig` from
the same Secret mount. Credentials are never baked into the image.

Gitea HTTPS metadata/API defaults to the internal
`http://gitea-http.external.svc.cluster.local:3000` service; set
`GITEA_SERVER_URL` to override it. The SSH host `git.nicholstech.org` is routed to
`gitea-ssh.external.svc.cluster.local:2222`. Its host-key lookup alias is exactly
`[git.nicholstech.org]:2222`, preserving the already trusted port-2222 key rather
than accidentally matching an old port-22 key. Strict host-key checking remains
on. The GitHub credential helper responds only to HTTPS GitHub requests.

## Upstream and CI ownership

Homelab sync/build automation belongs in `.gitea/workflows`; Gitea selects that
directory before upstream's `.github/workflows`. Keep upstream workflows intact
and disable Actions on the GitHub backup so its inherited publishing workflows
do not attempt PyPI or GHCR releases. Gitea is authoritative and GitHub is its
verified backup. Automated upstream merges must preserve the overlay and stop
on conflicts or validation failures before publishing a deployment update.
