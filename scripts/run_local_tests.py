"""Run backend tests with an in-memory test config, never production defaults.

The real config is parsed once for its attribute names, without executing it or
copying any values. This lets source-contract tests inspect its shape while all
subsequent reads/imports of config.py and .env files remain forbidden.
"""
import ast
from pathlib import Path
import os
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def config_attribute_names(source):
    """Return only declared Config names; parsing never evaluates default values."""
    tree = ast.parse(source)
    classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Config'
    ]
    if len(classes) != 1:
        raise ValueError('Source-contract tests require a declarative Config class')
    config_class = classes[0]
    if config_class.bases or config_class.decorator_list:
        raise ValueError('Source-contract tests require a declarative Config class')
    for node in tree.body:
        if node is config_class or isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'config'
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == 'Config'
            and not node.value.args
            and not node.value.keywords
        ):
            continue
        raise ValueError('Source-contract tests require declarative Config attributes')
    names = set()
    for node in config_class.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    raise ValueError('Unsupported Config attribute declaration')
                names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            # A class docstring does not declare any configuration fields.
            continue
        elif isinstance(node, ast.Pass):
            continue
        else:
            raise ValueError('Source-contract tests require declarative Config attributes')
    return frozenset(names)


def make_audit(root, runtime):
    root = Path(root).resolve()
    runtime = Path(runtime).resolve()
    config = root / 'cpm_back/config.py'
    legacy_config_cache = root / 'cpm_back/config.pyc'
    config_cache = root / 'cpm_back/__pycache__'

    def audit(event, args):
        if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            compiled_config = (
                path.parent == config_cache
                and path.name.startswith('config.')
                and path.suffix == '.pyc'
            )
            environment_file = root in path.parents and path.name.startswith('.env')
            if path in (config, legacy_config_cache) or compiled_config or environment_file:
                raise PermissionError('Local test runner refuses production configuration reads')
        elif event == 'socket.connect':
            address = args[1]
            if isinstance(address, tuple):
                if address[0] not in ('127.0.0.1', '::1', 'localhost'):
                    raise PermissionError('Local test runner refuses non-loopback connection')
            elif isinstance(address, (str, bytes)):
                path = Path(os.fsdecode(address)).resolve()
                if runtime not in path.parents:
                    raise PermissionError('Local test runner refuses unknown UNIX socket')
            else:
                raise PermissionError('Local test runner refuses unknown socket address')
        elif event == 'socket.getaddrinfo' and args[0] not in ('localhost', '127.0.0.1', '::1', None):
            raise PermissionError('Local test runner refuses non-loopback DNS')

    return audit


TEST_ENVIRONMENT = {
    'ENV': 'test', 'APP_ENV': 'test', 'FLASK_ENV': 'testing',
    'MYSQL_HOST': '127.0.0.1', 'MYSQL_PORT': '33077', 'MYSQL_USER': 'root',
    'MYSQL_PASSWORD': '', 'MYSQL_DATABASE': 'cpm_synthetic_test_only',
    'MYSQL_POOL_SIZE': '1',
    'MONGODB_URI': 'mongodb://127.0.0.1:27077/?directConnection=true',
    'MONGODB_DB_NAME': 'cpm_synthetic_test_only',
    'JWT_SECRET_KEY': 'synthetic-local-test-secret',
    'SECRET_KEY': 'synthetic-local-test-secret',
    'AWS_ACCESS_KEY_ID': 'synthetic-test-key',
    'AWS_SECRET_ACCESS_KEY': 'synthetic-test-secret',
    'AWS_EC2_METADATA_DISABLED': 'true',
}
TEST_CONFIG = dict(
    ENV='test', SECRET_KEY='synthetic-local-test-secret',
    JWT_SECRET_KEY='synthetic-local-test-secret', JWT_ALGORITHM='HS256',
    JWT_EXPIRATION_HOURS=1,
    MYSQL_HOST='127.0.0.1', MYSQL_PORT=33077, MYSQL_USER='root',
    MYSQL_PASSWORD='', MYSQL_DATABASE='cpm_synthetic_test_only', MYSQL_POOL_SIZE=1,
    MONGODB_URI='mongodb://127.0.0.1:27077/?directConnection=true',
    MONGODB_DB_NAME='cpm_synthetic_test_only', CORS_ORIGINS=[], CORS_ORIGINS_EXTRA='',
)


def main(argv=None):
    # This source-only snapshot never imports/executes the production config.
    source_names = config_attribute_names((ROOT / 'cpm_back/config.py').read_text())
    sys.dont_write_bytecode = True
    runtime = Path(os.environ.get('CPM_LOCAL_TEST_RUNTIME', '/tmp/cpm-isolated-runtime'))
    sys.addaudithook(make_audit(ROOT, runtime))
    os.environ.update(TEST_ENVIRONMENT)
    module = types.ModuleType('cpm_back.config')
    module.Config = type('Config', (), TEST_CONFIG)
    module.config = module.Config()
    module._local_test_source_attribute_names = source_names
    sys.modules[module.__name__] = module
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    arguments = [sys.argv[0], *(sys.argv[1:] if argv is None else argv)]
    if len(arguments) == 1:
        arguments.extend(['discover', '-s', 'tests', '-v'])
    unittest.main(module=None, argv=arguments)


if __name__ == '__main__':
    main()
