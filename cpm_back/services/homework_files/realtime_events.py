import json


def queue_thread_event(cursor, thread_id, event_type, entity_id=None, payload=None, ttl='1 DAY'):
    """Persist a small realtime signal in the same transaction as the domain change."""
    cursor.execute(
        'INSERT INTO homework_realtime_outbox '
        '(thread_id,event_type,entity_id,payload_json,expires_at) '
        f'VALUES (%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL {ttl}))',
        (thread_id, event_type, entity_id, json.dumps(payload) if payload is not None else None),
    )


def queue_user_event(cursor, role, user_id, event_type, entity_id=None, payload=None, ttl='1 DAY'):
    envelope = {
        'room': f'user:{role}:{int(user_id)}',
        'data': payload or ({'entity_id': entity_id} if entity_id is not None else {}),
    }
    cursor.execute(
        'INSERT INTO homework_realtime_outbox '
        '(thread_id,event_type,entity_id,payload_json,expires_at) '
        f'VALUES (NULL,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6),INTERVAL {ttl}))',
        (event_type, entity_id, json.dumps(envelope)),
    )


def queue_submission_changed(cursor, student_id, homework_id, submission_id=None):
    queue_user_event(
        cursor,
        'student',
        student_id,
        'submission.changed',
        submission_id,
        {
            'homework_id': int(homework_id),
            'student_id': int(student_id),
            'submission_id': submission_id,
        },
    )


def queue_job_progress(cursor, student_id, job):
    queue_user_event(
        cursor,
        'student',
        student_id,
        'job.progress',
        None,
        {
            'job': {
                key: job.get(key)
                for key in ('id', 'homework_id', 'status', 'stage', 'progress', 'error_code')
            },
        },
    )


def create_notification(cursor, role, user_id, kind, homework_id=None, student_id=None, thread_id=None):
    cursor.execute(
        'INSERT INTO notifications '
        '(recipient_role,recipient_id,kind,homework_id,student_id,thread_id) '
        'VALUES (%s,%s,%s,%s,%s,%s)',
        (role, user_id, kind, homework_id, student_id, thread_id),
    )
    notification_id = cursor.lastrowid
    queue_user_event(
        cursor,
        role,
        user_id,
        'notification.created',
        notification_id,
        {'entity_id': notification_id},
    )
    return notification_id
