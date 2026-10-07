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
import sys
import tempfile
import time
import tomllib
import uuid


def docker(*args, input=None, check=True, timeout=120):
    return subprocess.run(
        ['docker', *args], input=input, text=True, capture_output=True,
        check=check, timeout=timeout,
    )


def report_failure(containers, api_key, error):
    """Print bounded diagnostics only for this test's synthetic containers.

    Never dump docker inspect/config/environment: CI has production credentials,
    but none are passed to these network-isolated containers. Redact even the
    generated test API key, including if an exception contains it.
    """
    def emit(value):
        print(str(value).replace(api_key, "<redacted-smoke-key>")[-12000:], file=sys.stderr)

    emit(f"Smoke failure: {type(error).__name__}: {error}")
    if isinstance(error, subprocess.CalledProcessError):
        emit(error.stderr or error.stdout or "Docker command failed without output")
    for container in containers:
        emit(f"Synthetic smoke container: {container}")
        for args in (
            ('inspect', '--format', '{{json .State}}', container),
            ('logs', '--tail', '100', container),
        ):
            try:
                result = docker(*args, check=False, timeout=15)
                emit(result.stdout)
                emit(result.stderr)
            except Exception as diagnostic_error:
                emit(f"Diagnostic unavailable: {type(diagnostic_error).__name__}")


def verify_ssh_config(output):
    """Check OpenSSH's effective configuration without opening a connection."""
    values = {}
    for line in output.splitlines():
        key, _, value = line.partition(' ')
        values.setdefault(key, []).append(value)
    expected = {
        'hostname': 'gitea-ssh.external.svc.cluster.local',
        'port': '2222',
        'user': 'git',
        'hostkeyalias': '[git.nicholstech.org]:2222',
        'stricthostkeychecking': 'true',
        'userknownhostsfile': '/run/secrets/homelab/known_hosts',
        'identitiesonly': 'yes',
        'batchmode': 'yes',
        'forwardagent': 'no',
    }
    for key, value in expected.items():
        assert values.get(key) == [value], f'Unexpected SSH {key}: {values.get(key)}'
    assert '/run/secrets/homelab/identity' in values.get('identityfile', [])


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
                skill = pathlib.Path.home() / ".agents/skills/homelab-engineer/SKILL.md"
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text("---\\nname: homelab-engineer\\ndescription: smoke fixture\\n---\\nSkill body\\n")
                def get(path, authenticated=True):
                    request = urllib.request.Request(base + path, headers=headers if authenticated else {})
                    return urllib.request.urlopen(request, timeout=5)
                assert get("/skills/homelab-engineer").status == 200
                assert json.load(get("/skills/homelab-engineer"))["content"].strip() == "Skill body"
                assert json.load(get("/skills/read?name=homelab-engineer"))["content"].strip() == "Skill body"
                for path in ("/skills/not-installed", "/skills/%2e%2e%2fetc%2fpasswd"):
                    try:
                        get(path)
                    except urllib.error.HTTPError as error:
                        assert error.code == 404, (path, error.code)
                    else:
                        raise AssertionError("Unknown or path-like skill name was accepted: " + path)
                try:
                    get("/skills/homelab-engineer", authenticated=False)
                except urllib.error.HTTPError as error:
                    assert error.code == 401, error.code
                else:
                    raise AssertionError("Unauthenticated skill access succeeded")
                request = urllib.request.Request(base + "/execute?wait=10", headers=headers,
                    data=json.dumps({"command": COMMAND, "cwd": "/home/user/workspace"}).encode())
                result = json.load(urllib.request.urlopen(request, timeout=20))
                assert result.get("exit_code") == 0, result
                config = pathlib.Path("/home/user/.kube/config").read_text()
                assert "tokenFile: /var/run/secrets/kubernetes.io/serviceaccount/token" in config
                assert "smoke-test-placeholder" not in config
                assert pathlib.Path("/home/user/.config/tea/config.yml").stat().st_mode & 0o777 == 0o600
                '''.replace('COMMAND', repr(command)).replace('\n                ', '\n')
                docker('exec', '-i', container, 'python', '-', input=probe)
                # ssh -G parses the system include and rejects unsafe ownership/modes.
                # No network or usable private key is needed for this regression.
                for user in ('0:0', '1000:1000'):
                    verify_ssh_config(docker('exec', '--user', user, container,
                                             'ssh', '-G', '-T',
                                             'git.nicholstech.org').stdout)
                tools = ['bash', 'git', 'ssh', 'scp', 'sftp', 'rsync', 'curl', 'wget',
                         'jq', 'yq', 'rg', 'nano', 'python3', 'sqlite3', 'openssl',
                         'dig', 'kubectl', 'helm', 'flux', 'talosctl', 'kustomize',
                         'terraform', 'tofu', 'ansible', 'ansible-playbook', 'cilium',
                         'hubble', 'stern', 'ping', 'traceroute', 'tracepath', 'mtr',
                         'ip', 'ss', 'netstat', 'arp', 'bridge', 'ethtool', 'tcpdump',
                         'nc', 'socat', 'nmap', 'iperf3', 'whois', 'lsof', 'fping',
                         'arping', 'conntrack', 'nft', 'iptables', 'tea', 'gh', 'sops',
                         'node', 'dotnet']
                docker('exec', container, 'python', '-c',
                       'import shutil; tools=' + repr(tools) + '; assert all(shutil.which(x) for x in tools)')
                for executable, expected_version in {
                    'terraform': '1.16.5', 'tofu': '1.13.1', 'stern': '1.34.0',
                    'cilium': 'v0.20.1', 'hubble': 'v1.19.4', 'ansible': '2.21.5',
                }.items():
                    version = docker('exec', container, executable, '--version').stdout
                    assert expected_version in version, (executable, expected_version, version)
                capture = docker('exec', container, 'tcpdump', '-i', 'lo', '-c', '1',
                                 check=False, timeout=10)
                assert capture.returncode != 0 and 'permission' in (capture.stdout + capture.stderr).lower(), (
                    capture.returncode, capture.stdout, capture.stderr)
                docker('stop', '--time', '15', container)
                docker('rm', container)
                containers.remove(container)
        print(f'PASS: Open Terminal {expected}; source install and pip check; toolchain versions; '
              'UID 0/drop ALL/no-new-privileges; API authentication/execution/skill loading; '
              'local packet capture correctly denied without capabilities; '
              'strict internal Gitea SSH configuration; workspace survives replacement')
    except Exception as error:
        report_failure(containers, api_key, error)
        raise
    finally:
        for container in containers:
            docker('rm', '-f', container, check=False)
        docker('volume', 'rm', volume, check=False)


if __name__ == '__main__':
    main()
