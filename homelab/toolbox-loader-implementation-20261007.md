# Maintained toolbox and native skill loading — 2026-10-07

The homelab image keeps the Gitea `open-terminal` fork's pinned base and installs the fork's application source. The added CLI payload is reviewed in `tools.lock.json`; each downloaded archive or wheel is checked against a SHA-256 value recorded from the first-party distribution endpoint. The image build also writes the exact resolved Debian package versions to `/opt/homelab/system-tools.versions` for the candidate artifact.

## Pinned CLI versions

| CLI | Version | First-party artifact | SHA-256 |
| --- | --- | --- | --- |
| kubectl | 1.36.1 | [Kubernetes](https://dl.k8s.io/release/v1.36.1/bin/linux/amd64/kubectl) | `629d3f410e09bf49b64ae7079f7f0bda1191efed311f7d37fdbab0ad5b0ec2b7` |
| Talos CLI | 1.13.8 | [SideroLabs](https://github.com/siderolabs/talos/releases/download/v1.13.8/talosctl-linux-amd64) | `406b56f9e4ff03b1557cc941b1f163aec8a6ebb36e28f0bbbe6d083589529261` |
| Flux | 2.9.5 | [Flux](https://github.com/fluxcd/flux2/releases/download/v2.9.5/flux_2.9.5_linux_amd64.tar.gz) | `b853df82adfd7736f580692f9f734473d571606307139f8fd20c2a80dd1ff473` |
| Helm | 3.20.0 | [Helm](https://get.helm.sh/helm-v3.20.0-linux-amd64.tar.gz) | `dbb4c8fc8e19d159d1a63dda8db655f9ffa4aac1b9a6b188b34a40957119b286` |
| Kustomize | 5.8.2 | [Kubernetes SIGs](https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.2/kustomize_v5.8.2_linux_amd64.tar.gz) | `06af0a202c2b831207d0173f9c9cdb1b30abceca0747cb3fbb72792d26055c95` |
| yq | 4.54.1 | [mikefarah](https://github.com/mikefarah/yq/releases/download/v4.54.1/yq_linux_amd64) | `8e34fc298390875de416e6a4afcb8cabeceb25d9aa8506c1a2f9353cf702ea5f` |
| tea | 0.16.0 | [Gitea](https://gitea.com/gitea/tea/releases/download/v0.16.0/tea-0.16.0-linux-amd64.xz) | `92e0c966c98be0c6ca4c80b1912d08ff7887c7cb55e123542fa541842d875149` |
| Node.js | 22.22.2 | [Node.js](https://nodejs.org/dist/v22.22.2/node-v22.22.2-linux-x64.tar.xz) | `88fd1ce767091fd8d4a99fdb2356e98c819f93f3b1f8663853a2dee9b438068a` |
| .NET SDK | 10.0.401 | [Microsoft](https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.401/dotnet-sdk-10.0.401-linux-x64.tar.gz) | `51c8b999af9e8dd9998c9edc5944e19a90788862068acd38694e098889054ce8c23d4f0c5cccfa16bf187d044562359e5ee69a9f8ad0bbe913ba90311fbce25b` |
| SOPS | 3.13.3 | [SOPS](https://github.com/getsops/sops/releases/download/v3.13.3/sops-v3.13.3.linux.amd64) | `e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b` |
| Terraform | 1.16.5 | [HashiCorp](https://releases.hashicorp.com/terraform/1.16.5/terraform_1.16.5_linux_amd64.zip) | `2bc2fcfff033265c9e02ca0351f01794eb122f62a9b2a49a3294b9e49eaab5e4` |
| OpenTofu | 1.13.1 | [OpenTofu](https://github.com/opentofu/opentofu/releases/download/v1.13.1/tofu_1.13.1_linux_amd64.tar.gz) | `378ada19d4bc70c43732004e8159be771b23b9a5afdf059e5f8a2b3fa2c70a69` |
| Ansible Core | 2.21.5 | [PyPI](https://files.pythonhosted.org/packages/57/6f/7321059b2d1a5ea3c7e60807a5269942d93b2c11a2fc89ba8e4a3e827891/ansible_core-2.21.5-py3-none-any.whl) | `08886f9f3e23d129aa98a01271c4a47833516e1cb7c737a246788cb474f41222` |
| Stern | 1.34.0 | [Stern](https://github.com/stern/stern/releases/download/v1.34.0/stern_1.34.0_linux_amd64.tar.gz) | `7754adfa653939240f7d20fff4ada9b69cda40c9e70732301f67bb8045f1ef3e` |
| Cilium CLI | 0.20.1 | [Cilium](https://github.com/cilium/cilium-cli/releases/download/v0.20.1/cilium-linux-amd64.tar.gz) | `24e817dcfcc8a12e325ce7547617bcbcf171c5b0b335bb24d2bb1207ba047f61` |
| Hubble CLI | 1.19.4 | [Cilium](https://github.com/cilium/hubble/releases/download/v1.19.4/hubble-linux-amd64.tar.gz) | `c7c37a5f27ed2ae2d5495c6ddbecc61cebbb841508e77af3c131e96f005ed247` |

The six added tools use the first-party amd64 artifacts. The SHA-256 values were independently computed from the downloaded artifacts on 2026-10-07 and are checked again in the image build. Ansible Core's wheel is verified before pip installs it with the dependency constraints in `ansible-requirements.txt`. No speed-test tool is included.

## Diagnostic packages

The Debian package install adds `conntrack`, `dnsutils`, `ethtool`, `fping`, `iproute2`, `iptables`, `iputils-arping`, `iputils-ping`, `iputils-tracepath`, `iperf3`, `lsof`, `mtr-tiny`, `net-tools`, `netcat-openbsd`, `nmap`, `nftables`, `openssl`, `rsync`, `sqlite3`, `socat`, `tcpdump`, `traceroute`, `whois` and `wget`. Together with the base, this provides `dig`, `ip`, `ss`, `netstat`, `arp`, `bridge`, `ping`, `nc`, and the other requested commands. The image writes exact resolved package versions to `/opt/homelab/system-tools.versions`. The base itself remains pinned by digest, and its dated Debian snapshot keeps package resolution repeatable. The build accepts the snapshot's expired timestamp metadata while APT continues to verify repository signatures.

`tcpdump` is present for saved pcap analysis and command compatibility. Runtime security continues to drop every Linux capability and use `no-new-privileges`; local live capture is expected to fail for lack of packet-capture permission. Do not add `CAP_NET_RAW` or `CAP_NET_ADMIN`. Use a controlled remote capture only where the addressed host already permits it.

## Skill endpoint compatibility

OpenWebUI 0.11.4 requests a skill body at `/skills/{URL-encoded-name}`; Open Terminal 0.14.0 previously exposed `/skills/read?name=...`. The new route uses the same `verify_api_key` dependency and reads only a skill returned by the current discovered skill list. It compares the decoded route name with the discovered frontmatter name and then reads that skill's discovered `SKILL.md` path. It does not form a filesystem path from route input. The legacy query endpoint stays registered before the dynamic path route, so `/skills/read?name=...` keeps its existing behavior.

The image smoke checks both successful URL forms, unknown names, encoded path-like input, and missing authentication. It checks the diagnostics command inventory and exact versions of the newly pinned CLIs. With all capabilities dropped, its tcpdump probe expects a permission failure.

## Validation and publishing

The fork's `Homelab fork validation / image-smoke` Gitea workflow builds the exact pull request head and runs `homelab/smoke.py` against it, including fresh and replaced persistent-home generations. The workflow is the candidate image build path; it does not publish a runtime tag. The `homelab-upgrade` workflow is the existing publisher: it tests and builds a reviewed main candidate, publishes the immutable version/source-SHA tag to `git.nicholstech.org/nichols-homelab/open-terminal`, and submits a separate digest-pinned GitOps update. Deployment and reconciliation remain separate verification work.

Worker validation completed before image CI: 33 homelab synchronization tests passed, Python compilation passed for the modified application/smoke/installer modules, JSON lock parsing passed, and `git diff --check` passed. Focused FastAPI `TestClient` checks passed for alias and legacy reads, bearer auth, unknown skill and encoded path-like name. A published candidate digest and image-smoke receipt are recorded after the protected Gitea PR workflow runs; this worker environment has no usable container build daemon.
