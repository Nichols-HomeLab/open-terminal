import base64
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock, call

import yaml


SPEC = importlib.util.spec_from_file_location('upstream_sync', Path(__file__).parents[1] / 'upstream-sync.py')
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
HEAD = 'a' * 40
BASE = 'b' * 40
IMAGE = sync.IMAGE + ':0.14.0-homelab-sha-' + HEAD + '@sha256:' + 'c' * 64
OLD_IMAGE = sync.IMAGE + ':old@sha256:' + 'd' * 64
DEPLOYMENT = '''# Preserve comments and unrelated settings.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: open-terminal
spec:
  template:
    spec:
      serviceAccountName: openwebui-agent
      containers:
        - name: open-terminal
          image: OLD_IMAGE
          resources:
            limits: {memory: 8Gi}
'''.replace('OLD_IMAGE', OLD_IMAGE)
CRONJOB = '''apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: github-bundle-backup-workspace}
spec: {resources: {requests: {storage: 20Gi}}}
---
apiVersion: batch/v1
kind: CronJob
metadata: {name: github-bundle-backup}
spec:
  schedule: "17 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: OLD_IMAGE
'''.replace('OLD_IMAGE', OLD_IMAGE)


class ImageUpdateTests(unittest.TestCase):
    def test_multidocument_update_preserves_pvc_and_other_fields(self):
        path = 'clusters/random/open-terminal/github-bundle-backup.yaml'
        changed = sync.update_image(CRONJOB, sync.TARGETS[path], IMAGE)
        before = list(yaml.safe_load_all(CRONJOB))
        after = list(yaml.safe_load_all(changed))
        self.assertEqual(before[0], after[0])
        self.assertEqual(changed, CRONJOB.replace(OLD_IMAGE, IMAGE))

    def test_deployment_only_changes_selected_image(self):
        path = 'clusters/random/open-terminal/deployment.yaml'
        changed = sync.update_image(DEPLOYMENT, sync.TARGETS[path], IMAGE)
        self.assertTrue(changed.startswith('# Preserve comments'))
        self.assertEqual(changed, DEPLOYMENT.replace(OLD_IMAGE, IMAGE))

    def test_unexpected_repository_and_ambiguous_text_fail_closed(self):
        target = sync.TARGETS['clusters/random/open-terminal/deployment.yaml']
        with self.assertRaisesRegex(sync.Failure, 'unexpected image'):
            sync.update_image(DEPLOYMENT.replace(sync.IMAGE, 'unrelated/image'), target, IMAGE)
        with self.assertRaisesRegex(sync.Failure, 'Ambiguous'):
            sync.update_image(DEPLOYMENT + '# ' + OLD_IMAGE + '\n', target, IMAGE)

    def test_mutable_or_foreign_target_is_rejected(self):
        target = sync.TARGETS['clusters/random/open-terminal/deployment.yaml']
        for image in (sync.IMAGE + ':latest', IMAGE.replace(sync.IMAGE, 'outside/image'),
                      IMAGE.replace('0.14.0-homelab-', ''), IMAGE.replace(HEAD, HEAD[:12])):
            with self.subTest(image=image), self.assertRaises(sync.Failure):
                sync.update_image(DEPLOYMENT, target, image)


class VersionedImageTests(unittest.TestCase):
    def test_versions_preserve_prerelease_and_local_information(self):
        for original, expected in {'0.14.0': '0.14.0', '00.014.00': '0.14.0',
                '0.15.0rc1': '0.15.0-rc1', '0.15.0.dev1': '0.15.0.dev1',
                '0.15.0rc1.dev2+ABC.3': '0.15.0-rc1.dev2_abc.3',
                '1.2.3.post1': '1.2.3.post1'}.items():
            with self.subTest(version=original):
                version = sync.project_version('[project]\nversion = "' + original + '"')
                self.assertEqual(version, expected)
                self.assertIsNotNone(sync.IMAGE_REFERENCE.fullmatch(
                    sync.versioned_image_tag(version, HEAD) + '@sha256:' + 'c' * 64))

    def test_incomplete_or_unrecognized_versions_are_not_guessed(self):
        for value in ('1.2', 'latest', '1!2.3.4', '1.2.3-preview7'):
            with self.subTest(version=value), self.assertRaises(sync.Failure):
                sync.project_version('[project]\nversion = "' + value + '"')
        with self.assertRaisesRegex(sync.Failure, '128-character'):
            sync.versioned_image_tag('1.2.3_' + 'a' * 80, HEAD)

    def test_publish_uses_candidate_version_and_full_source_sha(self):
        upgrade = sync.Upgrade.__new__(sync.Upgrade)
        digest = sync.IMAGE + '@sha256:' + 'c' * 64
        def command(args, **kwargs):
            output = '["' + digest + '"]' if args[:3] == ['docker', 'image', 'inspect'] else ''
            return subprocess.CompletedProcess(args, 0, stdout=output)
        with patch.dict(sync.os.environ, {'REGISTRY_TOKEN': 'test', 'REGISTRY_USERNAME': 'test'}), \
                patch.object(sync, 'run', side_effect=command) as run:
            result = upgrade.publish('candidate', HEAD,
                sync.project_version('[project]\nversion="0.14.0"'))
        self.assertEqual(result, IMAGE)
        self.assertIn(['docker', 'tag', 'candidate', IMAGE.split('@')[0]],
            [c.args[0] for c in run.call_args_list])

    def test_deployment_requires_matching_version_commit_and_digest_on_both_resources(self):
        upgrade = sync.Upgrade.__new__(sync.Upgrade)
        upgrade.api = Mock()
        upgrade.api.head.return_value = BASE
        for image, expected in ((IMAGE, BASE), (IMAGE.replace('0.14.0', '0.14.1'), None),
                (IMAGE.replace(HEAD, 'e' * 40), None), (IMAGE.replace('0.14.0-homelab-', ''), None)):
            with self.subTest(image=image):
                upgrade.files = lambda revision: {path: ({}, text.replace(OLD_IMAGE, image))
                    for path, text in zip(sync.TARGETS, (DEPLOYMENT, CRONJOB))}
                self.assertEqual(upgrade.deployed(HEAD, '0.14.0'), expected)

    def test_owning_repository_policy_failure_blocks_deployment(self):
        api = Mock()
        policy = 'def validate_images(document, errors, source, exceptions):\n    errors.append("test policy rejection")\n'
        api.request.return_value = {'content': base64.b64encode(policy.encode()).decode()}
        with self.assertRaisesRegex(sync.Failure, 'owning GitOps repository rejected'):
            sync.validate_gitops_image_policy(api, BASE, {'deployment.yaml': DEPLOYMENT})
        self.assertIn('?ref=' + BASE, api.request.call_args.args[0])


class MergeApi:
    def __init__(self, base=BASE, head=HEAD, repo=sync.FORK):
        self.current = base
        self.pr_head = head
        self.repo = repo
        self.merges = []

    def head(self, repo):
        return self.current

    def request(self, path, data=None):
        if data:
            self.merges.append(data)
            self.current = data['head_commit_id']
        else:
            return {'head': {'sha': self.pr_head, 'repo': {'full_name': self.repo}}}


class MergeTests(unittest.TestCase):
    def test_only_exact_tested_head_is_fast_forwarded(self):
        api = MergeApi()
        sync.merge_exact(api, sync.FORK, 3, HEAD, BASE)
        self.assertEqual(api.merges, [{'do': 'fast-forward-only', 'head_commit_id': HEAD,
            'force_merge': False, 'delete_branch_after_merge': False}])

    def test_main_advance_or_pr_change_never_calls_merge(self):
        for api in (MergeApi(base='e' * 40), MergeApi(head='f' * 40)):
            with self.assertRaises(sync.Failure):
                sync.merge_exact(api, sync.FORK, 3, HEAD, BASE)
            self.assertEqual(api.merges, [])

    def test_external_fork_pull_request_is_not_automatically_merged(self):
        api = MergeApi(repo='another-owner/open-terminal')
        with self.assertRaisesRegex(sync.Failure, 'authoritative repository'):
            sync.merge_exact(api, sync.FORK, 3, HEAD, BASE)
        self.assertEqual(api.merges, [])


class PullRequestValidationTests(unittest.TestCase):
    def test_waits_for_native_pr_success_on_exact_commit(self):
        api = Mock()
        api.request.side_effect = [[], [{'id': 1, 'context': sync.REQUIRED_PR_CONTEXT, 'status': 'pending'}],
            [{'id': 2, 'context': sync.REQUIRED_PR_CONTEXT, 'status': 'success'}]]
        elapsed = [0]
        sync.wait_for_pr_validation(api, sync.FORK, HEAD, timeout=31,
            sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n), clock=lambda: elapsed[0])
        self.assertEqual(elapsed[0], 30)
        self.assertTrue(all(HEAD in c.args[0] for c in api.request.call_args_list))

    def test_latest_pending_does_not_reuse_old_success(self):
        api = Mock()
        api.request.return_value = [
            {'id': 1, 'context': sync.REQUIRED_PR_CONTEXT, 'status': 'success'},
            {'id': 2, 'context': sync.REQUIRED_PR_CONTEXT, 'status': 'pending'}]
        with self.assertRaisesRegex(sync.Failure, 'did not succeed'):
            sync.wait_for_pr_validation(api, sync.FORK, HEAD, timeout=0)

    def test_failed_or_skipped_validation_blocks_merge(self):
        for state in ('failure', 'error', 'cancelled', 'skipped'):
            with self.subTest(state=state):
                api = Mock()
                api.request.return_value = [{'id': 1, 'context': sync.REQUIRED_PR_CONTEXT, 'status': state}]
                with self.assertRaisesRegex(sync.Failure, 'reported ' + state):
                    sync.wait_for_pr_validation(api, sync.FORK, HEAD)

    def test_manual_status_is_not_a_native_pr_validation_substitute(self):
        api = Mock()
        api.request.return_value = [{'id': 1, 'context': 'homelab/image-smoke', 'status': 'success'}]
        with self.assertRaisesRegex(sync.Failure, 'did not succeed'):
            sync.wait_for_pr_validation(api, sync.FORK, HEAD, timeout=0)

    def test_validation_workflow_has_no_repository_secrets_or_branch_skips(self):
        path = Path(__file__).parents[2] / '.gitea/workflows/homelab-pr-validation.yml'
        text = path.read_text()
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(list(workflow['on']), ['pull_request'])
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['image-smoke']
        self.assertNotIn('if', job)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('docker push', text)
        self.assertNotIn('docker login', text)
        checkout = job['steps'][0]['with']
        self.assertEqual(checkout['ref'], '${{ github.event.pull_request.head.sha }}')
        self.assertEqual(checkout['persist-credentials'], 'false')
        self.assertEqual(sync.REQUIRED_PR_CONTEXT, workflow['name'] + ' / image-smoke (pull_request)')


class BackupTests(unittest.TestCase):
    def test_newer_default_branch_requires_proven_ancestry(self):
        ancestor = Mock(return_value=True)
        sync.verify_backup(sync.FORK, HEAD, fetch=lambda: {'object': {'sha': BASE}}, ancestor=ancestor)
        ancestor.assert_called_once_with(BASE)

    def test_private_gitops_backup_uses_approved_github_credential(self):
        with patch.dict(sync.os.environ, {'GH_BACKUP_TOKEN': 'private-backup-token'}), \
                patch.object(sync, 'Api') as api:
            api.return_value.request.return_value = {'object': {'sha': HEAD}}
            sync.verify_backup(sync.GITOPS, HEAD)
            api.assert_called_once_with('private-backup-token', 'https://api.github.com', auth_scheme='Bearer')
            api.return_value.request.assert_called_once_with(
                '/repos/Nichols-HomeLab/k3s-fluxcd/git/ref/heads/main', missing=True)

    def test_mirror_delay_is_polled_to_exact_ref(self):
        replies = iter([None, {'object': {'sha': BASE}}, {'object': {'sha': HEAD}}])
        elapsed = [0]
        sync.verify_backup(sync.FORK, HEAD, timeout=11, fetch=lambda: next(replies),
            sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n), clock=lambda: elapsed[0])
        self.assertEqual(elapsed[0], 10)

    def test_stale_mirror_has_finite_deadline(self):
        elapsed = [0]
        with self.assertRaisesRegex(sync.Failure, 'Gitea changes retained'):
            sync.verify_backup(sync.FORK, HEAD, timeout=11,
                fetch=lambda: {'object': {'sha': BASE}},
                sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n), clock=lambda: elapsed[0])
        self.assertEqual(elapsed[0], 11)


class CredentialTests(unittest.TestCase):
    def test_git_route_and_credential_scope_follow_internal_api(self):
        with patch.dict(sync.os.environ, {'GITEA_API_URL': 'http://gitea-api.external.svc:3000/api/v1',
                'GITEA_AUTOMATION_TOKEN': 'forge-token', 'GH_BACKUP_TOKEN': 'backup-token'}, clear=True):
            upgrade = sync.Upgrade('/tmp')
            self.assertEqual(upgrade.git_url,
                'http://gitea-api.external.svc:3000/Nichols-HomeLab/open-terminal.git')
            env = upgrade.git_auth_env()
            self.assertEqual(env['GIT_CONFIG_KEY_0'], 'http.' + upgrade.git_url + '/.extraheader')
            self.assertNotIn('forge-token', upgrade.git_url)

    def test_candidate_environment_does_not_inherit_tokens_or_git_auth(self):
        env = sync.public_env({'PATH': '/bin', 'HOME': '/temporary', 'GITEA_AUTOMATION_TOKEN': 'private',
            'REGISTRY_TOKEN': 'private', 'GITHUB_TOKEN': 'private', 'GIT_CONFIG_VALUE_0': 'private',
            'ACTIONS_RUNTIME_TOKEN': 'private', 'DOCKER_CONFIG': '/credentials', 'PYTHONPATH': '/untrusted'})
        self.assertEqual(env['PATH'], '/bin')
        self.assertFalse(any('private' in value for value in env.values()))
        self.assertNotIn('DOCKER_CONFIG', env)
        self.assertNotIn('PYTHONPATH', env)


class TagTests(unittest.TestCase):
    def test_only_absent_tags_are_added_and_conflicts_stop(self):
        source = {'refs/tags/v1': HEAD, 'refs/tags/v2': BASE}
        self.assertEqual(sync.missing_tags(source, {'refs/tags/v1': HEAD}), {'refs/tags/v2': BASE})
        with self.assertRaisesRegex(sync.Failure, 'no tags overwritten'):
            sync.missing_tags(source, {'refs/tags/v1': BASE})

    def test_alias_tags_get_distinct_staging_refs_and_atomic_push(self):
        upgrade = sync.Upgrade.__new__(sync.Upgrade)
        upgrade.git_url = 'http://internal/repo.git'
        upgrade.git_auth_env = lambda: {}
        calls = []
        def git(*args, **kwargs):
            calls.append(args)
            if args[:3] == ('ls-remote', '--refs', '--tags'):
                return HEAD + '\trefs/tags/v1\n' + HEAD + '\trefs/tags/v1.0' if args[3] == sync.UPSTREAM else ''
            if args[0] == 'rev-parse': return HEAD
            return ''
        upgrade.git = git
        upgrade.sync_tags()
        fetch = next(c for c in calls if c[0] == 'fetch')
        self.assertEqual(len(set(x.split(':', 1)[1] for x in fetch[3:])), 2)
        push = next(c for c in calls if c[0] == 'push')
        self.assertEqual(push[1], '--atomic')
        self.assertFalse(any(x.startswith('+') for x in push))

    def test_tag_backup_does_not_accept_same_commit_with_different_tag_object(self):
        elapsed = [0]
        with self.assertRaisesRegex(sync.Failure, 'Gitea tags retained'):
            sync.verify_tag_backup({'refs/tags/v1': HEAD}, lambda: {'refs/tags/v1': BASE}, timeout=5,
                sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n), clock=lambda: elapsed[0])
        self.assertEqual(elapsed[0], 5)


class RecoveryTests(unittest.TestCase):
    def test_already_deployed_still_verifies_gitops_backup(self):
        upgrade = sync.Upgrade.__new__(sync.Upgrade)
        upgrade.checkout = Path('/tmp')
        upgrade.git_url = 'http://internal/repo.git'
        upgrade.git_auth_env = lambda: {}
        upgrade.git = lambda *args, **kwargs: HEAD if args[0] == 'rev-parse' else (
            '[project]\nversion="0.14.0"' if args[0] == 'show' else '')
        upgrade.sync_tags = lambda: {'refs/tags/v1': HEAD}
        upgrade.verify_tags = Mock()
        upgrade.deployed = lambda head, version: BASE
        with patch.object(sync, 'verify_backup') as backup, \
                patch.object(sync, 'run', return_value=subprocess.CompletedProcess([], 0)):
            self.assertIsNone(upgrade.execute())
        self.assertEqual(backup.call_args_list, [call(sync.FORK, HEAD), call(sync.GITOPS, BASE)])
        upgrade.verify_tags.assert_called_once_with({'refs/tags/v1': HEAD})


class GitOpsTests(unittest.TestCase):
    def test_both_files_are_committed_atomically_with_blob_preconditions(self):
        sources = dict(zip(sync.TARGETS, [DEPLOYMENT, CRONJOB]))
        calls = []
        class Api:
            def head(self, repo): return BASE
            def request(self, path, data=None, **kwargs):
                calls.append((path, data))
                if '/branches/' in path: return None
                if path.endswith('/contents'):
                    return {'commit': {'sha': HEAD}}
                return {}
        upgrade = sync.Upgrade.__new__(sync.Upgrade)
        upgrade.api = Api()
        upgrade.gitops_pr_url = None
        def files(revision):
            return {path: ({'sha': 'blob-' + str(i)},
                text if revision == BASE else text.replace(OLD_IMAGE, IMAGE))
                for i, (path, text) in enumerate(sources.items())}
        upgrade.files = files
        upgrade.pr = lambda *args: {'html_url': 'https://example/pr/1', 'number': 1}
        with patch.object(sync, 'merge_exact') as merge, patch.object(sync, 'validate_gitops_image_policy') as policy:
            self.assertEqual(upgrade.deploy(IMAGE, HEAD), HEAD)
            merge.assert_called_once_with(upgrade.api, sync.GITOPS, 1, HEAD, BASE)
            policy.assert_called_once_with(upgrade.api, BASE,
                {path: text.replace(OLD_IMAGE, IMAGE) for path, text in sources.items()})
        changes = next(data['files'] for path, data in calls if path.endswith('/contents'))
        self.assertEqual(len(changes), 2)
        for i, item in enumerate(changes):
            self.assertEqual(item['sha'], 'blob-' + str(i))
            self.assertIn(IMAGE, base64.b64decode(item['content']).decode())
        branch_create = next(data for path, data in calls if path.endswith('/branches'))
        self.assertEqual(branch_create['old_ref_name'], BASE)


class ConflictWorkflowTests(unittest.TestCase):
    def test_conflict_candidate_retains_only_trusted_gitea_workflows(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=repo,
                    env=sync.public_env(), stderr=subprocess.DEVNULL, text=True).strip()
            git('init', '-q')
            (repo / '.github/workflows').mkdir(parents=True)
            (repo / '.github/workflows/release.yml').write_text('upstream publisher')
            git('add', '.'); git('commit', '-qm', 'upstream')
            upstream = git('rev-parse', 'HEAD')
            (repo / '.gitea/workflows').mkdir(parents=True)
            (repo / '.gitea/workflows/homelab.yml').write_text('trusted policy')
            git('add', '.'); git('commit', '-qm', 'local CI')
            base = git('rev-parse', 'HEAD')
            upgrade = sync.Upgrade.__new__(sync.Upgrade)
            upgrade.checkout = repo
            class Api:
                def request(self, *a, **kw): return None
            upgrade.api = Api()
            published = []
            upgrade.push = lambda branch, cwd: published.append(git('rev-parse', 'HEAD'))
            upgrade.pr = lambda *a: {'html_url': 'https://example/pr/1'}
            upgrade.retain_conflict(repo, upstream, base, 'automation/review', 'conflict')
            self.assertEqual((repo / '.gitea/workflows/homelab.yml').read_text(), 'trusted policy')
            self.assertEqual((repo / '.github/workflows/release.yml').read_text(), 'upstream publisher')
            self.assertEqual(len(published), 1)
            self.assertEqual(git('diff', base, '--', '.gitea'), '')


if __name__ == '__main__':
    unittest.main()
