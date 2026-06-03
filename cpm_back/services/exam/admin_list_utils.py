"""Пагинация и фильтры для админ-списков тестов."""

DEFAULT_PAGE = 1
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MIN_SEARCH_LENGTH = 2
MAX_SEARCH_STUDENT_IDS = 50


def parse_page_limit(page_raw, limit_raw):
    try:
        page = int(page_raw) if page_raw is not None else DEFAULT_PAGE
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    try:
        limit = int(limit_raw) if limit_raw is not None else DEFAULT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    page = max(1, page)
    limit = min(max(1, limit), MAX_LIMIT)
    skip = (page - 1) * limit
    return page, limit, skip


def build_pagination(total, page, limit):
    total_pages = max(1, (total + limit - 1) // limit) if total else 1
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": total_pages,
        "hasNext": page < total_pages,
        "hasPrev": page > 1,
    }


def normalize_search_query(search_raw):
    if not search_raw:
        return ""
    return str(search_raw).strip()
