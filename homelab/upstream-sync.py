#!/usr/bin/env python3
"""Bounded, tested upstream integration with immutable GitOps image updates.

Run from the trusted fork's main checkout. No production Kubernetes credentials
are needed. Failed candidates and pull requests remain available for inspection.
"""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import yaml

FORK = 'Nichols-HomeLab/open-terminal'
GITOPS = 'Nichols-HomeLab/k3s-fluxcd'
GITEA = 'https://git.nicholstech.org'
UPSTREAM = 'https://github.com/open-webui/open-terminal.git'
IMAGE = 'git.nicholstech.org/nichols-homelab/open-terminal'
REQUIRED_PR_CONTEXT = 'Homelab fork validation / image-smoke (pull_request)'
RELEASE_PATTERN = r'[0-9]+\.[0-9]+\.[0-9]+'
VERSION_PATTERN = (RELEASE_PATTERN + r'(?:-(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?'
                   r'(?:\.dev[0-9]+)?(?:_[a-z0-9]+(?:[._-][a-z0-9]+)*)?')
PYTHON_VERSION = re.compile(r'(?P<release>' + RELEASE_PATTERN + r')'
    r'(?:(?P<pre>a|b|rc)(?P<pre_number>[0-9]+))?'
    r'(?:\.post(?P<post>[0-9]+))?(?:\.dev(?P<dev>[0-9]+))?'
    r'(?:\+(?P<local>[a-z0-9]+(?:[._-][a-z0-9]+)*))?', re.IGNORECASE)
IMAGE_REFERENCE = re.compile(re.escape(IMAGE) + r':(?P<version>' + VERSION_PATTERN
    + r')-homelab-sha-(?P<head>[0-9a-f]{40})@sha256:(?P<digest>[0-9a-f]{64})')
TARGETS = {
    'clusters/random/open-terminal/deployment.yaml': ('Deployment', 'open-terminal', 'open-terminal'),
    'clusters/random/open-terminal/github-bundle-backup.yaml': ('CronJob', 'github-bundle-backup', 'backup'),
}


class Failure(RuntimeError):
    pass


def project_version(text):
    version = tomllib.loads(text).get('project', {}).get('version')
    parsed = PYTHON_VERSION.fullmatch(version) if isinstance(version, str) else None
    if not parsed:
        raise Failure('Unsupported candidate version: require x.y.z with optional a/b/rc, .post, .dev or +local suffix; no stable version is inferred')
    normalized = '.'.join(str(int(part)) for part in parsed['release'].split('.'))
    if parsed['pre']:
        normalized += '-' + parsed['pre'].lower() + str(int(parsed['pre_number']))
    for suffix in ('post', 'dev'):
        if parsed[suffix] is not None:
            normalized += '.' + suffix + str(int(parsed[suffix]))
    if parsed['local']:
        normalized += '_' + parsed['local'].lower()
    return normalized


def versioned_image_tag(version, head):
    if not re.fullmatch(VERSION_PATTERN, version) or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise Failure('Publishing requires the candidate x.y.z version and exact 40-character commit SHA')
    tag = version + '-homelab-sha-' + head
    if len(tag) > 128:
        raise Failure('Versioned image tag exceeds Docker\'s 128-character limit')
    return IMAGE + ':' + tag


def validate_gitops_image_policy(api, revision, files):
    """Use the owning repository's image rules at the exact inspected base."""
    data = api.request(f'/repos/{GITOPS}/contents/scripts/validate-manifests.py?ref={revision}')
    with tempfile.TemporaryDirectory(prefix='gitops-image-policy-') as directory:
        script = Path(directory) / 'validate-manifests.py'
        script.write_bytes(base64.b64decode(data['content']))
        runner = '''import importlib.util,json,sys,yaml
spec = importlib.util.spec_from_file_location('gitops_policy', sys.argv[1])
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
errors = []
for path, text in json.load(sys.stdin).items():
    for document in yaml.safe_load_all(text):
        if document:
            policy.validate_images(document, errors, path, set())
if errors:
    print('\\n'.join(errors))
    raise SystemExit(1)
'''
        result = run(['python3', '-c', runner, str(script)], input=json.dumps(files),
                     cwd=directory, capture=False, check=False, timeout=60)
        if result.returncode:
            raise Failure('The owning GitOps repository rejected candidate image references; no GitOps commit created')


def public_env(source=None):
    """Candidate processes receive no forge, registry, or inherited CI tokens."""
    source = os.environ if source is None else source
    allowed = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TERM', 'TZ', 'TMPDIR',
               'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH',
               'SSL_CERT_FILE', 'SSL_CERT_DIR', 'CURL_CA_BUNDLE')
    env = {key: source[key] for key in allowed if key in source}
    env.update({'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat',
                'GIT_AUTHOR_NAME': 'Homelab upstream automation',
                'GIT_AUTHOR_EMAIL': 'automation@users.noreply.git.nicholstech.org',
                'GIT_COMMITTER_NAME': 'Homelab upstream automation',
                'GIT_COMMITTER_EMAIL': 'automation@users.noreply.git.nicholstech.org'})
    return env


def run(args, *, cwd=None, timeout=120, env=None, capture=True, input=None, check=True):
    result = subprocess.run(args, cwd=cwd, env=public_env() if env is None else env,
                            timeout=timeout, input=input, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode:
        # Never interpolate subprocess output: remote errors can include auth
        # material. Build/smoke output is intentionally streamed with a clean env.
        raise Failure(f'{args[0]} {args[1] if len(args) > 1 else ""} exited {result.returncode}')
    return result


class Api:
    def __init__(self, token, base=None, auth_scheme='token'):
        self.token = token
        self.base = (base or os.environ.get('GITEA_API_URL', GITEA + '/api/v1')).rstrip('/')
        self.auth_scheme = auth_scheme

    def request(self, path, data=None, method=None, missing=False):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = self.auth_scheme + ' ' + self.token
        request = urllib.request.Request(self.base + path,
            data=None if data is None else json.dumps(data).encode(), headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise Failure(f'Forge API {method or ("POST" if data is not None else "GET")} {path} returned HTTP {error.code}') from None

    def head(self, repo, branch='main'):
        data = self.request(f'/repos/{repo}/branches/{urllib.parse.quote(branch, safe="")}')
        return data['commit']['id']


def image_container(documents, target):
    kind, name, container_name = target
    matches = [d for d in documents if d and d.get('kind') == kind
               and d.get('metadata', {}).get('name') == name]
    if len(matches) != 1:
        raise Failure(f'Expected exactly one {kind}/{name}')
    spec = matches[0]['spec']
    if kind == 'CronJob':
        spec = spec['jobTemplate']['spec']
    containers = [c for c in spec['template']['spec']['containers'] if c['name'] == container_name]
    if len(containers) != 1:
        raise Failure(f'Expected exactly one container {container_name}')
    return containers[0]


def update_image(text, target, image):
    """Preserve formatting and prove that only the intended image field changed."""
    reference = IMAGE_REFERENCE.fullmatch(image)
    if not reference:
        raise Failure('Refusing an image outside the immutable fork registry reference')
    versioned_image_tag(reference['version'], reference['head'])
    documents = list(yaml.safe_load_all(text))
    expected = copy.deepcopy(documents)
    container = image_container(expected, target)
    old = container['image']
    if old == image:
        return text
    if not old.startswith(IMAGE + ':'):
        raise Failure('Refusing to replace an unexpected image repository')
    if text.count(old) != 1:
        raise Failure('Ambiguous image text; preserve the GitOps branch for manual review')
    container['image'] = image
    changed = text.replace(old, image, 1)
    if list(yaml.safe_load_all(changed)) != expected:
        raise Failure('GitOps change modified something other than the selected image')
    return changed


def merge_exact(api, repo, pr, head, base):
    """FF-only and pinned head close both main-advance and PR-head races."""
    if api.head(repo) != base:
        raise Failure(f'{repo} main advanced; candidate retained for a fresh tested run')
    details = api.request(f'/repos/{repo}/pulls/{pr}')
    if details.get('head', {}).get('repo', {}).get('full_name') != repo:
        raise Failure('Automatic merging is restricted to candidates in the authoritative repository')
    if details['head']['sha'] != head:
        raise Failure('Pull request head changed after validation')
    api.request(f'/repos/{repo}/pulls/{pr}/merge', {
        'do': 'fast-forward-only', 'head_commit_id': head,
        'force_merge': False, 'delete_branch_after_merge': False,
    })
    if api.head(repo) != head:
        raise Failure('Merge readback differs from the tested commit')


def wait_for_pr_validation(api, repo, head, timeout=2700,
                           sleep=time.sleep, clock=time.monotonic):
    """Require the native PR job to succeed on this exact SHA, never a skip."""
    deadline = clock() + timeout
    while True:
        statuses = api.request(f'/repos/{repo}/commits/{head}/statuses?limit=100')
        matching = [s for s in statuses if s.get('context') == REQUIRED_PR_CONTEXT]
        latest = max(matching, key=lambda s: s.get('id', 0)) if matching else None
        state = latest.get('status') if latest else None
        if state == 'success':
            return
        if state in ('failure', 'error', 'cancelled', 'canceled', 'skipped'):
            raise Failure(f'Required pull-request image validation reported {state}; candidate retained')
        if clock() >= deadline:
            raise Failure('Required pull-request image validation did not succeed within 45 minutes; candidate retained')
        sleep(min(15, max(0, deadline - clock())))


def verify_backup(repo, expected, timeout=60, fetch=None, ancestor=None,
                  sleep=time.sleep, clock=time.monotonic):
    """Compare the backup main ref; mirror configuration alone is not success."""
    github = Api(os.environ.get('GH_BACKUP_TOKEN', '').strip(),
                 'https://api.github.com', auth_scheme='Bearer')
    if fetch is None:
        fetch = lambda: github.request(f'/repos/{repo}/git/ref/heads/main', missing=True)
        def ancestor(actual):
            comparison = github.request(f'/repos/{repo}/compare/{expected}...{actual}', missing=True)
            return comparison and comparison.get('status') in ('ahead', 'identical')
    ancestor = ancestor or (lambda actual: False)
    deadline = clock() + timeout
    while True:
        result = fetch()
        actual = (result or {}).get('object', {}).get('sha')
        if actual and (actual == expected or ancestor(actual)):
            return
        if clock() >= deadline:
            raise Failure(f'{repo} GitHub main did not match {expected[:12]} within {timeout}s; Gitea changes retained')
        sleep(min(5, max(0, deadline - clock())))


def parse_refs(text):
    return dict(line.split('\t', 1)[::-1] for line in text.splitlines() if line)


def missing_tags(source, destination):
    conflicts = [ref for ref, sha in source.items() if ref in destination and destination[ref] != sha]
    if conflicts:
        raise Failure('Upstream tag differs from an existing Gitea tag; no tags overwritten: ' + ', '.join(conflicts[:10]))
    return {ref: sha for ref, sha in source.items() if ref not in destination}


def verify_tag_backup(expected, fetch, timeout=60, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        actual = fetch()
        if all(actual.get(ref) == sha for ref, sha in expected.items()):
            return
        if clock() >= deadline:
            raise Failure('GitHub upstream tags did not match within the backup deadline; Gitea tags retained')
        sleep(min(5, max(0, deadline - clock())))


class Upgrade:
    def __init__(self, checkout):
        self.checkout = Path(checkout)
        token = os.environ.get('GITEA_AUTOMATION_TOKEN', '').strip()
        if not token:
            raise Failure('GITEA_AUTOMATION_TOKEN is required')
        if not os.environ.get('GH_BACKUP_TOKEN', '').strip():
            raise Failure('GH_BACKUP_TOKEN is required to verify the private GitOps backup')
        self.api = Api(token)
        self.git_url = os.environ.get('GITEA_GIT_URL', self.api.base.removesuffix('/api/v1')
                                      + '/' + FORK + '.git')
        self.stage = 'inspect'
        self.pr_url = None
        self.gitops_pr_url = None

    def git(self, *args, cwd=None, **kwargs):
        return run(['git', *args], cwd=cwd or self.checkout, **kwargs).stdout.strip()

    def git_auth_env(self):
        env = public_env()
        username = os.environ.get('GITEA_USERNAME', 'homelab-engineer')
        auth = base64.b64encode((username + ':' + self.api.token).encode()).decode()
        env.update({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.' + self.git_url + '/.extraheader',
                    'GIT_CONFIG_VALUE_0': 'Authorization: Basic ' + auth})
        return env

    def push(self, branch, cwd):
        self.git('push', self.git_url, 'HEAD:refs/heads/' + branch,
                 cwd=cwd, env=self.git_auth_env())

    def sync_tags(self):
        upstream = parse_refs(self.git('ls-remote', '--refs', '--tags', UPSTREAM, timeout=30))
        origin = parse_refs(self.git('ls-remote', '--refs', '--tags', self.git_url,
                                     env=self.git_auth_env(), timeout=30))
        additions = missing_tags(upstream, origin)
        if additions:
            staged = {ref: 'refs/homelab-upstream-tags/' + sha + '/' + ref.removeprefix('refs/tags/')
                      for ref, sha in additions.items()}
            fetch_specs = [ref + ':' + staged[ref] for ref in additions]
            self.git('fetch', '--no-tags', UPSTREAM, *fetch_specs)
            for ref, sha in additions.items():
                if self.git('rev-parse', staged[ref]) != sha:
                    raise Failure('Upstream tag moved while fetching; no tags published')
            self.git('push', '--atomic', self.git_url,
                     *[staged[ref] + ':' + ref for ref in additions],
                     env=self.git_auth_env())
        return upstream

    def verify_tags(self, tags):
        verify_tag_backup(tags, lambda: parse_refs(self.git('ls-remote', '--refs', '--tags',
            'https://github.com/' + FORK + '.git', timeout=30)))

    def pr(self, repo, branch, title, body):
        pulls = self.api.request(f'/repos/{repo}/pulls?state=open&limit=100')
        existing = next((p for p in pulls if p['head']['ref'] == branch and p['base']['ref'] == 'main'), None)
        if existing:
            return existing
        return self.api.request(f'/repos/{repo}/pulls', {
            'base': 'main', 'head': branch, 'title': title, 'body': body,
        })

    def retain_conflict(self, worktree, upstream, base, branch, reason):
        # A raw upstream branch lacks .gitea, which could activate inherited
        # .github release workflows when the PR is opened. Carry only our
        # trusted workflow policy onto the upstream candidate before publishing.
        self.git('checkout', '--detach', upstream, cwd=worktree)
        self.git('restore', '--source=' + base, '--staged', '--worktree', '--', '.gitea', cwd=worktree)
        if run(['git', 'diff', '--cached', '--quiet'], cwd=worktree, check=False).returncode:
            self.git('commit', '-m', 'Preserve trusted homelab CI on upstream review candidate', cwd=worktree)
        existing = self.api.request(f'/repos/{FORK}/branches/{urllib.parse.quote(branch, safe="")}', missing=True)
        if existing:
            branch += '-' + self.git('rev-parse', '--short=12', 'HEAD', cwd=worktree)
        self.push(branch, worktree)
        pr = self.pr(FORK, branch, 'Resolve upstream integration ' + upstream[:12],
            'Automatic upstream integration stopped. Resolve and test before merging.\n\n'
            'Trusted `.gitea` workflows are retained to prevent inherited publishing jobs.\n\n' + reason)
        self.pr_url = pr['html_url']

    def files(self, revision):
        files = {}
        for path in TARGETS:
            data = self.api.request(f'/repos/{GITOPS}/contents/{path}?ref={revision}')
            files[path] = (data, base64.b64decode(data['content']).decode())
        return files

    def deployed(self, head, version):
        base = self.api.head(GITOPS)
        files = self.files(base)
        images = [image_container(list(yaml.safe_load_all(text)), TARGETS[path])['image']
                  for path, (_, text) in files.items()]
        matched = (len(set(images)) == 1 and
                   bool(re.fullmatch(re.escape(versioned_image_tag(version, head)) + r'@sha256:[0-9a-f]{64}', images[0])))
        return base if matched else None

    def deploy(self, image, source_head):
        reference = IMAGE_REFERENCE.fullmatch(image)
        if not reference or reference['head'] != source_head:
            raise Failure('GitOps image reference must name the exact tested source commit')
        base = self.api.head(GITOPS)
        files = self.files(base)
        changes = []
        candidates = {}
        for path, (data, text) in files.items():
            changed = update_image(text, TARGETS[path], image)
            candidates[path] = changed
            if changed != text:
                changes.append({'operation': 'update', 'path': path, 'sha': data['sha'],
                                'content': base64.b64encode(changed.encode()).decode()})
        if not changes:
            return base
        validate_gitops_image_policy(self.api, base, candidates)
        branch = 'automation/open-terminal-' + source_head[:12] + '-' + base[:12]
        # Branch creation pins the exact inspected main commit. The file API
        # supplies blob SHAs as a second guard against concurrent changes.
        existing = self.api.request(f'/repos/{GITOPS}/branches/{urllib.parse.quote(branch, safe="")}', missing=True)
        if existing:
            branch += '-' + str(time.time_ns())
        self.api.request(f'/repos/{GITOPS}/branches', {'new_branch_name': branch, 'old_ref_name': base})
        result = self.api.request(f'/repos/{GITOPS}/contents', {
            'branch': branch, 'message': 'Upgrade Open Terminal to tested fork ' + source_head[:12],
            'files': changes,
        })
        head = result['commit']['sha']
        # Read the remote commit back, not merely the submitted local YAML.
        readback = self.files(head)
        for path, (_, text) in readback.items():
            if text != update_image(files[path][1], TARGETS[path], image):
                raise Failure('GitOps API commit readback differs from validated image update')
        pr = self.pr(GITOPS, branch, 'Upgrade Open Terminal to ' + source_head[:12],
            'Deploy the fork image built and smoke-tested at `' + source_head + '`.\n\n'
            'Image: `' + image + '`. Both terminal and backup job references change atomically. '
            'YAML was parsed and compared to ensure only the two intended image fields changed. '
            'Flux and live workload health require the deployment audit.')
        self.gitops_pr_url = pr['html_url']
        merge_exact(self.api, GITOPS, pr['number'], head, base)
        return head

    def publish(self, local_image, head, version):
        token = os.environ.get('REGISTRY_TOKEN', '').strip()
        username = os.environ.get('REGISTRY_USERNAME', '').strip()
        if not token or not username:
            raise Failure('REGISTRY_TOKEN and REGISTRY_USERNAME are required for publishing')
        tag = versioned_image_tag(version, head)
        with tempfile.TemporaryDirectory(prefix='registry-auth-') as auth:
            env = public_env()
            env['DOCKER_CONFIG'] = auth
            run(['docker', 'login', 'git.nicholstech.org', '--username', username, '--password-stdin'],
                env=env, input=token, timeout=60)
            run(['docker', 'tag', local_image, tag], env=env)
            run(['docker', 'push', tag], env=env, timeout=900, capture=False)
            digests = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', tag], env=env).stdout)
            matching = [d for d in digests if d.startswith(IMAGE + '@sha256:')]
            if len(matching) != 1 or not re.fullmatch(re.escape(IMAGE) + r'@sha256:[0-9a-f]{64}', matching[0]):
                raise Failure('Published registry image did not produce one verified repository digest')
            # Ask the registry for this immutable object while authentication is
            # available; a local image ID is not a registry manifest digest.
            run(['docker', 'manifest', 'inspect', matching[0]], env=env, timeout=60)
            return tag + '@' + matching[0].split('@', 1)[1]

    def execute(self):
        self.stage = 'fetch upstream'
        self.git('fetch', '--no-tags', self.git_url,
                 '+refs/heads/main:refs/remotes/origin/main', env=self.git_auth_env())
        self.git('fetch', '--no-tags', UPSTREAM,
                 '+refs/heads/main:refs/remotes/upstream/main')
        base = self.git('rev-parse', 'refs/remotes/origin/main')
        upstream = self.git('rev-parse', 'refs/remotes/upstream/main')
        base_version = project_version(self.git('show', base + ':pyproject.toml'))
        self.stage = 'preserve upstream release tags'
        tags = self.sync_tags()
        already_merged = run(['git', 'merge-base', '--is-ancestor', upstream, base],
                            cwd=self.checkout, check=False).returncode == 0
        deployed = self.deployed(base, base_version) if already_merged else None
        if deployed:
            verify_backup(FORK, base)
            verify_backup(GITOPS, deployed)
            self.verify_tags(tags)
            print('Upstream already integrated and both GitOps references match ' + base)
            return None
        with tempfile.TemporaryDirectory(prefix='upstream-integration-') as temporary:
            worktree = Path(temporary) / 'candidate'
            self.git('worktree', 'add', '--detach', str(worktree), base)
            try:
                head = base
                pr = None
                if not already_merged:
                    self.stage = 'merge upstream'
                    branch = 'automation/upstream-' + upstream[:12] + '-' + base[:12]
                    result = run(['git', 'merge', '--no-ff', '--no-edit', upstream],
                                 cwd=worktree, check=False)
                    if result.returncode:
                        conflicts = self.git('diff', '--name-only', '--diff-filter=U', cwd=worktree)
                        self.git('merge', '--abort', cwd=worktree)
                        self.retain_conflict(worktree, upstream, base, branch,
                            'Conflicting paths:\n```\n' + conflicts + '\n```')
                        raise Failure('Upstream merge conflict; candidate pull request retained')
                    if run(['git', 'diff', '--quiet', base, 'HEAD', '--', '.gitea'],
                           cwd=worktree, check=False).returncode:
                        self.retain_conflict(worktree, upstream, base, branch,
                            'Upstream changed `.gitea` automation policy. Review that change explicitly.')
                        raise Failure('Upstream changed automation policy; safe candidate pull request retained')
                    head = self.git('rev-parse', 'HEAD', cwd=worktree)
                    existing = self.api.request(f'/repos/{FORK}/branches/{urllib.parse.quote(branch, safe="")}', missing=True)
                    if existing:
                        branch += '-' + head[:12]
                    self.push(branch, worktree)
                    pr = self.pr(FORK, branch, 'Integrate upstream ' + upstream[:12],
                        'Merge official upstream `' + upstream + '` into `' + base + '`.\n\n'
                        'Automatic merge requires candidate image build, dependency checks and isolated '
                        'terminal persistence/API smoke tests. Any version is eligible when compatible.')
                    self.pr_url = pr['html_url']
                self.stage = 'build candidate'
                version = project_version((worktree / 'pyproject.toml').read_text())
                local_image = 'open-terminal-candidate:' + head
                run(['docker', 'build', '--pull', '--build-arg', 'SOURCE_REVISION=' + head,
                     '--label', 'org.opencontainers.image.revision=' + head,
                     '--label', 'org.opencontainers.image.source=' + GITEA + '/' + FORK,
                     '-f', 'homelab/Dockerfile', '-t', local_image, '.'], cwd=worktree,
                    timeout=3600, capture=False)
                self.stage = 'test candidate'
                run(['python3', 'homelab/smoke.py', local_image], cwd=worktree, timeout=900, capture=False)
                if pr:
                    self.api.request(f'/repos/{FORK}/statuses/{head}', {
                        'context': 'homelab/image-smoke', 'state': 'success',
                        'description': 'Candidate image build and isolated API/persistence smoke tests passed',
                    })
                    self.stage = 'await required pull-request image validation'
                    wait_for_pr_validation(self.api, FORK, head)
                    self.stage = 'merge tested candidate'
                    merge_exact(self.api, FORK, pr['number'], head, base)
                elif self.api.head(FORK) != head:
                    raise Failure('Fork main advanced during recovery build; retry from current main')
                self.stage = 'verify fork backup'
                verify_backup(FORK, head)
                self.verify_tags(tags)
                self.stage = 'publish tested image'
                image = self.publish(local_image, head, version)
                self.stage = 'update GitOps'
                gitops_head = self.deploy(image, head)
                self.stage = 'verify GitOps backup'
                verify_backup(GITOPS, gitops_head)
                return {'source': head, 'upstream': upstream, 'image': image, 'gitops': gitops_head}
            finally:
                # All recoverable candidates were already pushed. This worktree
                # is exclusive to this invocation and never contains user work.
                run(['git', 'worktree', 'remove', '--force', str(worktree)],
                    cwd=self.checkout, check=False)


def notify(title, message, failure=False):
    url = os.environ.get('NTFY_URL', 'http://ntfy.notifications.svc.cluster.local/updates')
    request = urllib.request.Request(url, data=message.encode(), headers={
        'Title': title, 'Priority': 'high' if failure else 'default',
        'Tags': 'warning' if failure else 'white_check_mark',
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                if 200 <= response.status < 300:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        if attempt < 2:
            time.sleep(2)
    raise Failure('ntfy updates notification failed after three attempts')


def main():
    upgrade = None
    try:
        upgrade = Upgrade(Path.cwd())
        result = upgrade.execute()
        if result:
            notify('Open Terminal GitOps upgrade submitted',
                'Tested upstream integration and image published. Gitea and GitHub refs verified.\n'
                + json.dumps(result, indent=2) + '\nFlux convergence and live workload health: not verified by CI.')
            print(json.dumps(result))
        return 0
    except Exception as error:
        stage = upgrade.stage if upgrade else 'configuration'
        # Exceptions from networking/subprocesses may include caller data; only
        # our carefully constructed Failure messages are safe to publish.
        reason = str(error) if isinstance(error, Failure) else type(error).__name__
        message = f'Open Terminal automatic upgrade stopped at {stage}: {reason}. '
        if upgrade:
            message += ' '.join(u for u in (upgrade.pr_url, upgrade.gitops_pr_url) if u)
        print(message, file=sys.stderr)
        try:
            notify('Open Terminal cannot complete automatic upgrade', message, failure=True)
        except Failure as notification_error:
            print(str(notification_error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
