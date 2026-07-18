"""Mandatory guard for opt-in integration tests against the production bucket."""

def require_test_key(key):
    if not isinstance(key,str) or not key.startswith('_test/'):
        raise RuntimeError('S3 integration tests may mutate only _test/ keys')
    return key
