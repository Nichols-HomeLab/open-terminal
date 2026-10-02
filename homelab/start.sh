#!/bin/bash
set -euo pipefail
umask 077
mkdir -p "$HOME"/{workspace,worktrees,.kube,.ssh,.config/tea,.local/state/open-terminal/logs}
# Reference the projected token file so kubectl follows Kubernetes token rotation.
cat > "$HOME/.kube/config" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: homelab
  cluster:
    server: https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT_HTTPS:-443}
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
users:
- name: openwebui-agent
  user:
    tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token
contexts:
- name: homelab
  context:
    cluster: homelab
    user: openwebui-agent
current-context: homelab
EOF
export GH_TOKEN="$(cat /run/secrets/homelab/github-token)"
export GITEA_TOKEN="$(cat /run/secrets/homelab/gitea-token)"
export GITEA_SERVER_URL="${GITEA_SERVER_URL:-http://gitea-http.external.svc.cluster.local:3000}"
export GIT_SSH_COMMAND='ssh -i /run/secrets/homelab/identity -o IdentitiesOnly=yes -o UserKnownHostsFile=/run/secrets/homelab/known_hosts -o StrictHostKeyChecking=yes'
git config --global credential.https://github.com.helper /opt/homelab/git-credential.py
git config --global user.name 'Homelab Engineer'
git config --global user.email 'homelab-engineer@users.noreply.git.nicholstech.org'
git config --global init.defaultBranch main
git config --global push.default simple
git config --global pull.ff only
# tea does not support a token-file flag. Write its private runtime configuration
# outside the workspace; the mounted Secret remains the credential authority.
python - <<'PY'
import json, os, pathlib
p = pathlib.Path.home() / '.config/tea/config.yml'
p.write_text('logins:\n  - name: homelab\n    url: '+json.dumps(os.environ['GITEA_SERVER_URL'])+'\n    token: '+json.dumps(os.environ['GITEA_TOKEN'])+'\n    default: true\n')
p.chmod(0o600)
PY
export OPEN_TERMINAL_EXECUTE_DESCRIPTION='Homelab engineering terminal: bash, git, ssh, curl, jq, yq, rg, Python, Node, .NET, kubectl, Helm, Flux, Talos, Kustomize, SOPS, tea and gh. Persistent repositories: /home/user/workspace/<repo>; separate task worktrees: /home/user/worktrees/<repo>/<agent>. Gitea is primary; verify GitHub backups. Read the homelab-engineer skill. Kubernetes uses rotating service-account credentials. Use CLI fallback when MCP fails.'
exec python -m uvicorn open_terminal.main:app --host 0.0.0.0 --port 8000 --no-access-log
