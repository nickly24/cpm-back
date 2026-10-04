"""Run backend tests with an in-memory test config, never production defaults."""
from pathlib import Path
import os
import sys
import types
import unittest

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / 'cpm_back/config.py').resolve()
RUNTIME = Path(os.environ.get('CPM_LOCAL_TEST_RUNTIME', '/tmp/cpm-isolated-runtime')).resolve()

def audit(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path == CONFIG or (ROOT in path.parents and path.name.startswith('.env')):
            raise PermissionError('Local test runner refuses production configuration reads')
    elif event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple):
            if address[0] not in ('127.0.0.1', '::1', 'localhost'):
                raise PermissionError('Local test runner refuses non-loopback connection')
        elif isinstance(address, (str, bytes)):
            path = Path(os.fsdecode(address)).resolve()
            if RUNTIME not in path.parents:
                raise PermissionError('Local test runner refuses unknown UNIX socket')
        else:
            raise PermissionError('Local test runner refuses unknown socket address')
    elif event == 'socket.getaddrinfo' and args[0] not in ('localhost', '127.0.0.1', '::1', None):
        raise PermissionError('Local test runner refuses non-loopback DNS')

sys.addaudithook(audit)
os.environ.update({
    'ENV': 'test', 'APP_ENV': 'test', 'FLASK_ENV': 'testing',
    'MYSQL_HOST': '127.0.0.1', 'MYSQL_PORT': '33077', 'MYSQL_USER': 'root',
    'MYSQL_PASSWORD': '', 'MYSQL_DATABASE': 'cpm_synthetic_test_only',
    'MONGODB_URI': 'mongodb://127.0.0.1:27077/?directConnection=true',
    'MONGODB_DB_NAME': 'cpm_synthetic_test_only',
    'JWT_SECRET_KEY': 'synthetic-local-test-secret',
    'SECRET_KEY': 'synthetic-local-test-secret',
    'AWS_ACCESS_KEY_ID': 'synthetic-test-key',
    'AWS_SECRET_ACCESS_KEY': 'synthetic-test-secret',
    'AWS_EC2_METADATA_DISABLED': 'true',
})
module = types.ModuleType('cpm_back.config')
module.config = types.SimpleNamespace(
    ENV='test', SECRET_KEY='synthetic-local-test-secret',
    JWT_SECRET_KEY='synthetic-local-test-secret', JWT_ALGORITHM='HS256',
    JWT_EXPIRATION_HOURS=1,
    MYSQL_HOST='127.0.0.1', MYSQL_PORT=33077, MYSQL_USER='root',
    MYSQL_PASSWORD='', MYSQL_DATABASE='cpm_synthetic_test_only', MYSQL_POOL_SIZE=1,
    MONGODB_URI='mongodb://127.0.0.1:27077/?directConnection=true',
    MONGODB_DB_NAME='cpm_synthetic_test_only', CORS_ORIGINS=[], CORS_ORIGINS_EXTRA='',
)
sys.modules[module.__name__] = module
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
argv = [sys.argv[0], *sys.argv[1:]]
if len(argv) == 1:
    argv.extend(['discover', '-s', 'tests', '-v'])
unittest.main(module=None, argv=argv)
