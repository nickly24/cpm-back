"""Обратная совместимость: кэш теста перенесён в exam_memory_cache."""
from cpm_back.services.exam.exam_memory_cache import (
    get_test_document_cached,
    invalidate_published_tests_cache,
    invalidate_test_cache,
)

__all__ = [
    "get_test_document_cached",
    "invalidate_test_cache",
    "invalidate_published_tests_cache",
]
