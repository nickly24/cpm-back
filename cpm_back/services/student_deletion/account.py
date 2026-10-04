"""Reject old student sessions using the existing account tables."""
from .common import transaction, one


def load_live_student(student_id, *, cursor=None):
    if isinstance(student_id, bool):
        return None
    try:
        student_id = int(student_id)
        if not 0 < student_id <= 9223372036854775807:
            return None
        query = "SELECT s.id,s.full_name,s.group_id FROM students s WHERE s.id=%s AND EXISTS " \
                "(SELECT 1 FROM auth_users a WHERE a.role='student' AND a.ref_id=s.id)"
        if cursor is not None:
            student = one(cursor, query, (student_id,))
        else:
            with transaction() as current:
                student = one(current, query, (student_id,))
    except Exception:
        return None
    if student:
        student['role'] = 'student'
    return student
