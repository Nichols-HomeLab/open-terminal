# This immutable base preserves the tested CLI toolchain and published caches.
# Application code below is always installed from THIS fork checkout, not inherited.
# Base provenance and the independent-rebase path are documented in README.md.
FROM git.nicholstech.org/nichols-homelab/open-terminal:0.11.35-homelab-20261001@sha256:e543124b217105d8d629fd334090791cb1b3740575e87384e4d5afd3f5843857
USER root
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.source="https://git.nicholstech.org/Nichols-HomeLab/open-terminal" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"
WORKDIR /opt/open-terminal-src
COPY pyproject.toml README.md LICENSE ./
COPY open_terminal/ ./open_terminal/
# Upstream metadata is authoritative for both dependencies and application version.
RUN python -m pip install --no-cache-dir --upgrade . \
    && python -m pip check \
    && python -c 'import importlib.metadata as m, pathlib, tomllib; expected=tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]; assert m.version("open-terminal")==expected; pathlib.Path("/opt/open-terminal-source-version").write_text(expected)' \
    && mkdir -p /run/secrets/homelab /var/run/secrets/kubernetes.io/serviceaccount
COPY homelab/start.sh homelab/git-credential.py /opt/homelab/
COPY homelab/tools.lock.json homelab/install-tools.py /opt/homelab/
COPY homelab/ssh_config /etc/ssh/ssh_config.d/10-homelab.conf
# Docker seeds new named volumes from this directory. The inherited upstream
# HOME is UID 1000/mode 0700; our UID 0 runtime has no DAC_OVERRIDE capability.
# Normalize ownership at build time so a fresh persistent workspace can start.
RUN chmod 755 /opt/homelab/start.sh /opt/homelab/git-credential.py \
    && chown -R 0:0 /home/user \
    && chown 0:0 /home \
    && chmod 755 /home /home/user
ENV HOME=/home/user SHELL=/bin/bash DOTNET_ROOT=/opt/dotnet \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    PYTHONDONTWRITEBYTECODE=1 OPEN_TERMINAL_MULTI_USER=false \
    OPEN_TERMINAL_LOG_DIR=/home/user/.local/state/open-terminal/logs \
    GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/home/user/.gitconfig \
    KUBECONFIG=/home/user/.kube/config TALOSCONFIG=/run/secrets/homelab/talosconfig
WORKDIR /home/user
# Use the normal Python executable. The upstream console entrypoint's inherited
# file capability can fail with Kubernetes drop: [ALL] and no-new-privileges.
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/homelab/start.sh"]
EXPOSE 8000
