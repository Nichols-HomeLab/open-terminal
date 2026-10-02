#!/usr/bin/env python3
"""Smoke-test a built image using Docker, synthetic credentials and an isolated volume.

Usage: python3 homelab/smoke.py IMAGE
Runs no Kubernetes operations and requires no production credentials.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import tomllib
import uuid


def docker(*args, input=None, check=True, timeout=120):
    return subprocess.run(
        ['docker', *args], input=input, text=True, capture_output=True,
        check=check, timeout=timeout,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    name = 'open-terminal-smoke-' + uuid.uuid4().hex[:12]
    volume = name + '-home'
    api_key = secrets.token_urlsafe(32)
    containers = []
    docker('volume', 'create', volume)
    try:
        # Verify installed distribution, import location, and declared requirements.
        script = '''import importlib.metadata, pathlib, open_terminal
expected = EXPECTED
assert importlib.metadata.version("open-terminal") == expected
assert pathlib.Path("/opt/open-terminal-source-version").read_text() == expected
assert "/site-packages/" in open_terminal.__file__, open_terminal.__file__
'''.replace('EXPECTED', repr(expected))
        docker('run', '--rm', '--network', 'none', '--entrypoint', 'python',
               args.image, '-c', script)
        docker('run', '--rm', '--network', 'none', '--entrypoint', 'python',
               args.image, '-m', 'pip', 'check')
        with tempfile.TemporaryDirectory(prefix=name) as temp:
            temporary = Path(temp)
            credentials = temporary / 'homelab'
            serviceaccount = temporary / 'serviceaccount'
            credentials.mkdir()
            serviceaccount.mkdir()
            for filename in ('github-token', 'gitea-token', 'identity', 'host-identity', 'known_hosts', 'talosconfig'):
                (credentials / filename).write_text('smoke-test-placeholder\n')
                (credentials / filename).chmod(0o600)
            for filename in ('token', 'ca.crt'):
                (serviceaccount / filename).write_text('smoke-test-placeholder\n')
            environment = temporary / 'environment'
            environment.write_text('OPEN_TERMINAL_API_KEY=' + api_key + '\n'
                                   'KUBERNETES_SERVICE_HOST=127.0.0.1\n'
                                   'KUBERNETES_SERVICE_PORT_HTTPS=6443\n')
            environment.chmod(0o600)
            for generation in (1, 2):
                container = name + '-' + str(generation)
                docker('create', '--name', container, '--network', 'none',
                       '--user', '0:0', '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges:true',
                       '--mount', f'type=volume,source={volume},target=/home/user',
                       '--env-file', str(environment), args.image)
                containers.append(container)
                # docker cp works with remote/DinD daemons; host bind mounts do not.
                docker('cp', str(credentials) + '/.', container + ':/run/secrets/homelab/')
                docker('cp', str(serviceaccount) + '/.', container + ':/var/run/secrets/kubernetes.io/serviceaccount/')
                docker('start', container)
                readiness = '''import urllib.request
assert urllib.request.urlopen("http://127.0.0.1:8000/openapi.json", timeout=2).status == 200
'''
                for attempt in range(60):
                    result = docker('exec', container, 'python', '-c', readiness, check=False, timeout=10)
                    if result.returncode == 0:
                        break
                    state = json.loads(docker('inspect', container).stdout)[0]['State']
                    if not state['Running']:
                        raise RuntimeError('Runtime exited before becoming ready')
                    time.sleep(1)
                else:
                    raise RuntimeError('Runtime did not become ready within 60 attempts')
                command = (
                    "printf 'persistent-workspace-ok' > /home/user/workspace/smoke-persistence"
                    if generation == 1 else
                    "test \"$(cat /home/user/workspace/smoke-persistence)\" = persistent-workspace-ok"
                )
                probe = '''import json, os, pathlib, urllib.error, urllib.request
base = "http://127.0.0.1:8000"
for headers in ({}, {"Authorization": "Bearer incorrect-smoke-key"}):
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/execute", headers=headers), timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError("Unauthenticated terminal access succeeded")
headers = {"Authorization": "Bearer " + os.environ["OPEN_TERMINAL_API_KEY"], "Content-Type": "application/json"}
request = urllib.request.Request(base + "/execute?wait=10", headers=headers,
    data=json.dumps({"command": COMMAND, "cwd": "/home/user/workspace"}).encode())
result = json.load(urllib.request.urlopen(request, timeout=20))
assert result.get("exit_code") == 0, result
config = pathlib.Path("/home/user/.kube/config").read_text()
assert "tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token" in config
assert "smoke-test-placeholder" not in config
assert pathlib.Path("/home/user/.config/tea/config.yml").stat().st_mode & 0o777 == 0o600
'''.replace('COMMAND', repr(command))
                docker('exec', '-i', container, 'python', '-', input=probe)
                tools = ['bash', 'git', 'ssh', 'curl', 'wget', 'jq', 'yq', 'rg', 'nano',
                         'python3', 'kubectl', 'helm', 'flux', 'talosctl', 'kustomize',
                         'tea', 'gh', 'sops', 'node', 'dotnet']
                docker('exec', container, 'python', '-c',
                       'import shutil; tools=' + repr(tools) + '; assert all(shutil.which(x) for x in tools)')
                docker('stop', '--time', '15', container)
                docker('rm', container)
                containers.remove(container)
        print(f'PASS: Open Terminal {expected}; source install and pip check; required tools; '
              'UID 0/drop ALL/no-new-privileges; API authentication/execution; workspace survives replacement')
    finally:
        for container in containers:
            docker('rm', '-f', container, check=False)
        docker('volume', 'rm', volume, check=False)


if __name__ == '__main__':
    main()
