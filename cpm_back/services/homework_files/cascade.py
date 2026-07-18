def queue_and_delete_submission_data(cursor, *, homework_id=None, student_id=None):
    clauses=[];params=[]
    if homework_id is not None:clauses.append('sub.homework_id=%s');params.append(homework_id)
    if student_id is not None:clauses.append('sub.student_id=%s');params.append(student_id)
    if not clauses:raise ValueError('bounded entity filter required')
    where=' AND '.join(clauses);plain_where=where.replace('sub.','')
    cursor.execute('INSERT IGNORE INTO homework_s3_delete_queue (object_key,available_at,created_at) SELECT f.object_key,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6) FROM homework_submission_files f JOIN homework_submissions sub ON sub.id=f.submission_id WHERE '+where,tuple(params))
    cursor.execute('DELETE FROM homework_chat_threads WHERE '+plain_where,tuple(params))
    cursor.execute('DELETE FROM homework_submissions WHERE '+plain_where,tuple(params))
