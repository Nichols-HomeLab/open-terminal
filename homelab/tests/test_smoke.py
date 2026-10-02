"""Failure diagnostics must remain useful without exposing test credentials."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke', Path(__file__).parents[1] / 'smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class FailureDiagnosticsTests(unittest.TestCase):
    def test_collects_bounded_state_and_logs_and_redacts_key(self):
        key = 'synthetic-secret'
        output = io.StringIO()
        def docker(*args, **kwargs):
            self.assertFalse(kwargs['check'])
            self.assertEqual(kwargs['timeout'], 15)
            return subprocess.CompletedProcess(args, 0, 'x' * 13000 + key, 'Permission denied')
        with patch.object(smoke, 'docker', side_effect=docker) as command:
            with contextlib.redirect_stderr(output):
                smoke.report_failure(['smoke-only-1'], key, RuntimeError(key))
        self.assertEqual(command.call_args_list[0].args,
                         ('inspect', '--format', '{{json .State}}', 'smoke-only-1'))
        self.assertEqual(command.call_args_list[1].args,
                         ('logs', '--tail', '100', 'smoke-only-1'))
        text = output.getvalue()
        self.assertNotIn(key, text)
        self.assertIn('<redacted-smoke-key>', text)
        self.assertIn('Permission denied', text)
        self.assertLess(len(text), 25000)

    def test_unavailable_diagnostics_do_not_mask_original_failure(self):
        output = io.StringIO()
        with patch.object(smoke, 'docker', side_effect=subprocess.TimeoutExpired('docker', 15)):
            with contextlib.redirect_stderr(output):
                smoke.report_failure(['smoke-only-1'], 'synthetic-secret', RuntimeError('original failure'))
        self.assertIn('original failure', output.getvalue())
        self.assertEqual(output.getvalue().count('Diagnostic unavailable: TimeoutExpired'), 2)


if __name__ == '__main__':
    unittest.main()
