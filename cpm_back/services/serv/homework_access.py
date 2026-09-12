"""Shared guards for legacy homework actions and optional file-service metadata."""
from datetime import timezone


class HomeworkAccessError(ValueError):
    def __init__(self, code, http_status=403):
        super().__init__(code)
        self.code = code
        self.http_status = http_status

    def response(self):
        return {'status': False, 'error': self.code, 'http_status': self.http_status}


def scoped_proctor_id(actor, requested):
    if (actor or {}).get('role') == 'proctor':
        if requested is not None and str(requested) != str(actor['id']):
            raise HomeworkAccessError('student_not_in_current_group')
        return actor['id']
    return requested


def ensure_student_scope(cursor, actor, student_id, lock=False):
    if (actor or {}).get('role') != 'proctor':
        return
    cursor.execute(
        'SELECT s.id FROM students s JOIN proctors p ON p.group_id=s.group_id '
        'WHERE p.id=%s AND s.id=%s' + (' FOR UPDATE' if lock else ''), (actor['id'], student_id),
    )
    if not cursor.fetchone():
        raise HomeworkAccessError('student_not_in_current_group')


def ensure_legacy_editable(cursor, homework_id, student_id):
    # Lock the same submission row as the file service, including when no file
    # exists yet. This serializes a legacy grade with a student's first upload.
    try:
        cursor.execute(
            "INSERT INTO homework_submissions (homework_id,student_id,state) VALUES (%s,%s,'none') "
            'ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)', (homework_id, student_id),
        )
        cursor.execute(
            'SELECT state,current_file_id,draft_file_id FROM homework_submissions '
            'WHERE homework_id=%s AND student_id=%s FOR UPDATE', (homework_id, student_id),
        )
        sub = cursor.fetchone()
    except Exception as exc:
        if getattr(exc, 'errno', None) == 1146:
            return  # Main API is also deployed before the optional migration.
        raise
    if sub and (sub['state'] != 'none' or sub['current_file_id'] or sub['draft_file_id']):
        raise HomeworkAccessError('use_file_review', 409)


def attach_file_submissions(cursor, rows, homework_id=None, student_id=None):
    """One bounded query per list; keep legacy responses working without migration."""
    pairs = [(int(homework_id or row['homework_id']), int(student_id or row['student_id'])) for row in rows]
    found = {}
    if pairs:
        try:
            marks = ','.join(['(%s,%s)'] * len(pairs))
            cursor.execute(
                'SELECT id,homework_id,student_id,state,current_file_id,draft_file_id,'
                'submitted_at_utc,revision_comment FROM homework_submissions '
                f'WHERE (homework_id,student_id) IN ({marks})',
                tuple(value for pair in pairs for value in pair),
            )
            found = {(row['homework_id'], row['student_id']): row for row in cursor.fetchall()}
        except Exception as exc:
            if getattr(exc, 'errno', None) != 1146:
                raise
    for row, pair in zip(rows, pairs):
        sub = found.get(pair)
        state = sub['state'] if sub else None
        stamp = sub.get('submitted_at_utc') if sub else None
        if stamp:
            stamp = stamp.replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        row.update({
            'file_managed': bool(sub and (state != 'none' or sub['current_file_id'] or sub['draft_file_id'])),
            'file_submission_id': sub['id'] if sub else None,
            'file_submission_state': state,
            'submission_id': sub['id'] if sub else None,
            'submission_state': state,
            'has_file': bool(sub and sub['current_file_id']),
            'has_draft': bool(sub and sub['draft_file_id']),
            'submitted_at_utc': stamp,
            'revision_comment': sub.get('revision_comment') if sub else None,
        })
