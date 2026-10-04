"""Regression tests for isolated config inspection and runtime guardrails."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from scripts import run_local_tests as runner


class LocalTestRunnerTests(unittest.TestCase):
    def test_config_contract_parses_names_without_evaluating_defaults(self):
        source = """
class Config:
    SECRET_KEY = raise_if_executed()
    MYSQL_HOST: str = raise_if_executed()
    EXAMS_V2_ENABLED = False
config = Config()
"""
        self.assertEqual(
            runner.config_attribute_names(source),
            frozenset({'SECRET_KEY', 'MYSQL_HOST', 'EXAMS_V2_ENABLED'}),
        )

    def test_config_contract_rejects_dynamic_shapes_instead_of_missing_flags(self):
        for source in (
            'class Other: pass',
            'class Config(Base): pass',
            '@decorator\nclass Config: pass',
            'class Config: pass\nConfig.EXAMS_V2_ENABLED = False',
            'class Config: pass\nconfig = Config()\nsetattr(config, "EXAMS_V2_ENABLED", False)',
            'class Config:\n    if enabled:\n        EXAMS_V2_ENABLED = False',
            'class Config:\n    def __init__(self):\n        self.EXAMS_V2_ENABLED = False',
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                runner.config_attribute_names(source)

    def test_config_source_bytecode_and_env_access_are_blocked(self):
        audit = runner.make_audit(runner.ROOT, '/tmp/cpm-isolated-runtime')
        for path in (
            runner.ROOT / 'cpm_back/config.py',
            runner.ROOT / 'cpm_back/config.pyc',
            runner.ROOT / 'cpm_back/__pycache__/config.cpython-312.pyc',
            runner.ROOT / '.env',
            runner.ROOT / 'cpm_back/.env.production',
        ):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                audit('open', (str(path), 'r', 0))
        audit('open', (str(runner.ROOT / 'tests/test_exam_always_enabled.py'), 'r', 0))

    def test_config_symlink_cannot_bypass_read_guard(self):
        audit = runner.make_audit(runner.ROOT, '/tmp/cpm-isolated-runtime')
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'innocent-name.py'
            link.symlink_to(runner.ROOT / 'cpm_back/config.py')
            with self.assertRaises(PermissionError):
                audit('open', (str(link), 'r', 0))

    def test_connections_and_dns_allow_only_loopback(self):
        audit = runner.make_audit(runner.ROOT, '/tmp/cpm-isolated-runtime')
        for host in ('127.0.0.1', '::1', 'localhost'):
            with self.subTest(host=host):
                audit('socket.connect', (None, (host, 33077)))
                audit('socket.getaddrinfo', (host, 33077, 0, 0, 0))
        for host in ('203.0.113.1', 'example.invalid', '127.0.0.1.example.invalid'):
            with self.subTest(host=host), self.assertRaises(PermissionError):
                audit('socket.connect', (None, (host, 33077)))
            with self.subTest(dns=host), self.assertRaises(PermissionError):
                audit('socket.getaddrinfo', (host, 33077, 0, 0, 0))

    def test_unix_socket_must_be_inside_explicit_isolated_runtime(self):
        runtime = Path('/tmp/cpm-isolated-runtime').resolve()
        audit = runner.make_audit(runner.ROOT, runtime)
        audit('socket.connect', (None, str(runtime / 'mysql.sock')))
        for path in ('/tmp/mysql.sock', str(runtime / '../mysql.sock')):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                audit('socket.connect', (None, path))
        with self.assertRaises(PermissionError):
            audit('socket.connect', (None, object()))

    def test_actual_runner_keeps_guards_with_inherited_production_settings(self):
        # Exercise the entry point in a fresh process: no audit hook is removed
        # or relaxed in this process, and remote calls fail before opening a
        # connection or resolving a hostname.
        probe = textwrap.dedent('''\
            import importlib
            import importlib.util
            import os
            from pathlib import Path
            import socket
            import unittest
            config_module = importlib.import_module('cpm_back.config')

            class RunnerSafetyProbe(unittest.TestCase):
                def test_runtime_uses_only_synthetic_configuration(self):
                    config = config_module.config
                    self.assertEqual(config.MYSQL_HOST, '127.0.0.1')
                    self.assertEqual(config.MYSQL_PORT, 33077)
                    self.assertEqual(config.MYSQL_DATABASE, 'cpm_synthetic_test_only')
                    self.assertEqual(config.MYSQL_PASSWORD, '')
                    self.assertEqual(config.MYSQL_POOL_SIZE, 1)
                    self.assertEqual(config.MONGODB_DB_NAME, 'cpm_synthetic_test_only')
                    self.assertEqual(os.environ['MYSQL_HOST'], '127.0.0.1')
                    self.assertEqual(os.environ['MYSQL_PASSWORD'], '')
                    self.assertEqual(os.environ['MYSQL_POOL_SIZE'], '1')
                    self.assertIsInstance(config_module._local_test_source_attribute_names, frozenset)
                    self.assertIn('MYSQL_HOST', config_module._local_test_source_attribute_names)

                def test_real_audit_hooks_block_config_env_and_remote_access(self):
                    root = Path(__import__('scripts.run_local_tests', fromlist=['ROOT']).ROOT)
                    for path in (root / 'cpm_back/config.py', root / '.env.runner-probe'):
                        with self.assertRaises(PermissionError):
                            path.read_text()
                    spec = importlib.util.spec_from_file_location('forbidden_config', root / 'cpm_back/config.py')
                    with self.assertRaises(PermissionError):
                        spec.loader.exec_module(importlib.util.module_from_spec(spec))
                    with socket.socket() as connection:
                        with self.assertRaises(PermissionError):
                            connection.connect(('203.0.113.1', 3306))
                    with self.assertRaises(PermissionError):
                        socket.getaddrinfo('example.invalid', 3306)
        ''')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'runner_safety_probe.py').write_text(probe)
            environment = dict(os.environ)
            environment.update({
                'PYTHONPATH': directory,
                'MYSQL_HOST': 'example.invalid',
                'MYSQL_PASSWORD': 'inherited-placeholder-only',
                'MYSQL_POOL_SIZE': '99',
                'MONGODB_URI': 'mongodb://example.invalid:27017/placeholder',
            })
            completed = subprocess.run(
                [sys.executable, str(runner.ROOT / 'scripts/run_local_tests.py'), 'runner_safety_probe', '-v'],
                cwd=runner.ROOT, env=environment, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('Ran 2 tests', completed.stderr)


if __name__ == '__main__':
    unittest.main()
