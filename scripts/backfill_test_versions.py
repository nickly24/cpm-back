#!/usr/bin/env python3
"""Create immutable v2 definitions for all currently published tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpm_back.config import config
from cpm_back.db.mongo import get_mongo_db, init_mongo
from cpm_back.services.exam.test_versions import ensure_test_version
from cpm_back.services.exam.test_time import MOSCOW_TZ, to_datetime


def main():
    client = init_mongo(config)
    db = get_mongo_db()
    created_or_verified = 0
    failed = 0
    for test in db.tests.find({"published": {"$ne": False}}):
        try:
            normalized_dates = {}
            for key in ("startDate", "endDate"):
                parsed = to_datetime(test.get(key))
                if parsed:
                    normalized_dates[key] = parsed.astimezone(MOSCOW_TZ).isoformat()
            if normalized_dates:
                db.tests.update_one({"_id": test["_id"]}, {"$set": normalized_dates})
                test.update(normalized_dates)
            version = ensure_test_version(test)
            if version:
                created_or_verified += 1
        except Exception as error:
            failed += 1
            print(f"test {test.get('_id')}: {error}")
    client.close()
    print(f"versions ready: {created_or_verified}; failed: {failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
