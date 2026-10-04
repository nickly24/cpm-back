"""Server-owned storage scope and actual-schema dependency validation.

Never infer CASCADE. Every known child is selected and deleted explicitly; unknown
student columns/FK descendants block the operation before the first deletion.
"""
from .common import DeletionError, digest, dumps, quoted, rows, one
from .legacy import LEGACY_TABLES, LEGACY_DEPENDENCIES, collect_legacy, validate_legacy_schema
from .fk_scope import FK_QUERY, check_fk_scope

DIRECT = {name: 'student_id' for name in (
    'homework_sessions', 'exam_sessions', 'test_sessions', 'attendance',
    'class_day_attendance', 'zaps', 'student_credentials', 'student_progress',
    'student_card_progress', 'student_section_study_settings', 'Allratings',
    'rating_recalc_staging', 'classic_exam_assignments',
    'classic_exam_student_privileges', 'classic_exam_attempts',
    'telegram_bot_chats', 'homework_submissions', 'homework_file_jobs',
    'homework_object_ownership',
)}
CHILDREN = {
    'zap_img': ('zap_id', 'zaps', 'id'),
    'zap_dates': ('zap_id', 'zaps', 'id'),
    'classic_exam_assignment_members': ('assignment_id', 'classic_exam_assignments', 'id'),
    'classic_exam_attempt_members': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_attempt_commands': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_attempt_part_progress': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_presented_questions': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_attempt_question_usage': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_vote_rounds': ('attempt_id', 'classic_exam_attempts', 'id'),
    'classic_exam_votes': ('round_id', 'classic_exam_vote_rounds', 'id'),
    'classic_exam_appeals': ('attempt_id', 'classic_exam_attempts', 'id'),
    'homework_submission_files': ('submission_id', 'homework_submissions', 'id'),
}
HOMEWORK_OWNED = {'homework_submissions', 'homework_file_jobs', 'homework_submission_files', 'homework_object_ownership'}
ACCOUNT = {'students', 'auth_users', 'student_credentials'}
CONTROL = {'student_deletion_jobs', 'student_deletion_barriers', 'homework_student_purges',
           'homework_student_purge_objects', 'homework_purge_objects'}
REQUIRED = {'students', 'auth_users', 'homework_sessions', 'exam_sessions', 'test_sessions',
            'student_deletion_jobs', 'student_deletion_barriers', 'homework_submissions',
            'homework_submission_files', 'homework_file_jobs', 'homework_object_ownership',
            'homework_student_purges', 'homework_service_capabilities'}
MONGO = {'test_attempts': 'studentId', 'test_sessions': 'studentId', 'rate_rec': 'student_id'}
COUNT_KEYS = ('homeworks', 'homework_submissions', 'homework_files', 'exams', 'exam_attempts',
              'exam_results', 'tests', 'test_attempts', 'test_results', 'other_records')
LABELS = {
    'attendance': 'Посещаемость (старый журнал)', 'class_day_attendance': 'Посещаемость',
    'zaps': 'Заявки на отгул', 'zap_img': 'Вложения заявок на отгул', 'zap_dates': 'Даты отгулов',
    'student_card_progress': 'Прогресс карточек', 'student_progress': 'Старый учебный прогресс',
    'student_section_study_settings': 'Настройки обучения', 'Allratings': 'Рейтинг',
    'rating_recalc_staging': 'Черновики рейтинга', 'telegram_bot_chats': 'Связь с Telegram',
    'exam_admin_commands': 'Персональные квитанции экзаменов',
    'student_credentials': 'Реквизиты входа', 'auth_users': 'Учётные записи',
    'classic_exam_assignments': 'Назначения на экзамены',
    'classic_exam_student_privileges': 'Индивидуальные условия экзаменов',
    'rate_rec': 'Детализация рейтинга',
    'homework_chat_threads': 'Старые переписки по домашним работам',
    'homework_chat_messages': 'Сообщения старых переписок',
    'homework_chat_reads': 'Отметки прочтения старых переписок',
    'homework_chat_admin_followers': 'Подписки администраторов на старые переписки',
    'homework_realtime_presence': 'Старые статусы присутствия и набора текста',
    'homework_realtime_outbox': 'Старые события доставки уведомлений',
    'notifications': 'Уведомления ученика и о нём',
    'push_subscriptions': 'Push-подписки ученика',
    'classic_exam_assignment_members': 'Состав комиссии в назначениях ученика',
    'classic_exam_attempt_members': 'Состав комиссии в попытках ученика',
    'classic_exam_attempt_commands': 'Команды экзаменационных попыток',
    'classic_exam_attempt_part_progress': 'Прогресс частей экзаменов',
    'classic_exam_presented_questions': 'История выданных вопросов',
    'classic_exam_attempt_question_usage': 'История выбора вопросов',
    'classic_exam_vote_rounds': 'Раунды голосования по ответам',
    'classic_exam_votes': 'Оценки ответов комиссией',
    'classic_exam_appeals': 'История апелляций',
}


def schema(cursor):
    columns = {}
    for row in rows(cursor, 'SELECT TABLE_NAME,COLUMN_NAME,COLUMN_KEY,ORDINAL_POSITION FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION'):
        columns.setdefault(row['TABLE_NAME'], []).append(row)
    missing = REQUIRED - columns.keys()
    if missing:
        raise DeletionError('schema_not_ready', 'Не применена схема удаления: ' + ', '.join(sorted(missing)), 503)
    allowed = set(DIRECT) | set(CHILDREN) | LEGACY_TABLES | ACCOUNT | CONTROL | {'exam_admin_commands'}
    for table, cols in columns.items():
        if table not in allowed and any(c['COLUMN_NAME'] in {'student_id', 'studentId', 'target_student_id'} for c in cols):
            raise DeletionError('unknown_student_table', f'Нужна проверка персональной таблицы {table}.', 503)
    fks = rows(cursor, FK_QUERY)
    personal = set(DIRECT) | set(CHILDREN) | LEGACY_TABLES | ACCOUNT | {'exam_admin_commands'}
    for fk in fks:
        if fk['REFERENCED_TABLE_NAME'] in personal and fk['TABLE_NAME'] not in personal | CONTROL:
            raise DeletionError('unknown_student_dependency', 'Нужна проверка зависимости ' + fk['TABLE_NAME'], 503)
    pks = {table: [c['COLUMN_NAME'] for c in cols if c['COLUMN_KEY'] == 'PRI'] for table, cols in columns.items()}
    validate_legacy_schema(columns, pks)
    return columns, pks, fks


def selectors(columns, student_id):
    selected = {'students': ('id=%s', (student_id,)), 'auth_users': ("role='student' AND ref_id=%s", (student_id,))}
    for table, column in DIRECT.items():
        if table in columns:
            if column not in {c['COLUMN_NAME'] for c in columns[table]}:
                raise DeletionError('unsupported_schema', 'Неизвестная структура ' + table, 503)
            selected[table] = (quoted(column) + '=%s', (student_id,))
    for table, (column, parent, key) in CHILDREN.items():
        if table in columns:
            if parent not in selected:
                raise DeletionError('unsupported_schema', 'Отсутствует родитель ' + parent, 503)
            condition, args = selected[parent]
            selected[table] = (f'{quoted(column)} IN (SELECT {quoted(key)} FROM {quoted(parent)} WHERE {condition})', args)
    return selected


def deletion_order(selected, fks):
    remaining = set(selected)
    dependencies = {(fk['TABLE_NAME'], fk['REFERENCED_TABLE_NAME']) for fk in fks
                    if fk['TABLE_NAME'] in remaining and fk['REFERENCED_TABLE_NAME'] in remaining
                    and fk['TABLE_NAME'] != fk['REFERENCED_TABLE_NAME']}
    dependencies |= {(child, parent) for child, (_, parent, _) in CHILDREN.items() if child in remaining and parent in remaining}
    dependencies |= {(child, parent) for child, parent in LEGACY_DEPENDENCIES if child in remaining and parent in remaining}
    # Legacy installations need the same semantic order even without FK declarations.
    dependencies |= {(table, 'students') for table in remaining if table != 'students'}
    if 'classic_exam_attempts' in remaining and 'classic_exam_assignments' in remaining:
        dependencies.add(('classic_exam_attempts', 'classic_exam_assignments'))
    ordered = []
    while remaining:
        ready = sorted(table for table in remaining if not any(parent == table and child in remaining for child, parent in dependencies))
        if not ready:
            raise DeletionError('cyclic_student_schema', 'Циклическая зависимость требует отдельной миграции.', 503)
        ordered.extend(ready)
        remaining -= set(ready)
    return ordered


def mongo_filter(student_id, field):
    return {field: {'$in': [student_id, str(student_id)]}}


def summarize(sql, mongo):
    homework_ids = {str(r['homework_id']) for t in ('homework_sessions', 'homework_submissions') for r in sql.get(t, [])}
    exam_ids = {str(r['exam_id']) for t in ('exam_sessions', 'classic_exam_assignments', 'classic_exam_attempts', 'classic_exam_student_privileges') for r in sql.get(t, [])}
    test_ids = {'external:' + str(r['test_id']) for r in sql.get('test_sessions', [])}
    test_ids |= {'internal:' + str(r['testId']) for t in ('test_attempts', 'test_sessions') for r in mongo.get(t, []) if r.get('testId') is not None}
    primary = {'homework_sessions', 'exam_sessions', 'test_sessions', 'classic_exam_attempts'} | HOMEWORK_OWNED | ACCOUNT
    other = sum(len(value) for table, value in sql.items() if table not in primary) + len(mongo.get('rate_rec', []))
    return dict(homeworks=len(homework_ids), homework_submissions=len(sql.get('homework_submissions', [])),
                homework_files=len(sql.get('homework_submission_files', [])), exams=len(exam_ids),
                exam_attempts=len(sql.get('classic_exam_attempts', [])),
                exam_results=len(sql.get('exam_sessions', [])) + sum(r.get('calculated_grade') is not None for r in sql.get('classic_exam_attempts', [])),
                tests=len(test_ids), test_attempts=len(mongo.get('test_attempts', [])),
                test_results=len(sql.get('test_sessions', [])) + len(mongo.get('test_sessions', [])), other_records=other)


def collect(cursor, mongo_db, student_id, *, readiness=True, legacy_ref_context=None):
    columns, pks, fks = schema(cursor)
    from .file_consistency import check_file_consistency
    check_file_consistency(cursor, student_id, columns)
    if readiness:
        capability = one(cursor, "SELECT protocol_version FROM homework_service_capabilities WHERE service_name='student-purge' AND heartbeat_at>=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 60 SECOND)")
        if not capability or capability['protocol_version'] != 1:
            raise DeletionError('homework_service_unavailable', 'Сервис файлов не подтвердил готовность удаления.', 503)
        if 'user_import_jobs' in columns and one(cursor, "SELECT id FROM user_import_jobs WHERE status IN ('queued','running','rolling_back') LIMIT 1"):
            raise DeletionError('import_in_progress', 'Дождитесь завершения импорта пользователей.', 409)
    selected = selectors(columns, student_id)
    sql = {}
    for table, (condition, args) in selected.items():
        sql[table] = rows(cursor, f'SELECT * FROM {quoted(table)} WHERE {condition}', args)
        if not pks[table] and sql[table]:
            raise DeletionError('missing_primary_key', 'Для безопасного удаления нужен первичный ключ: ' + table, 503)
        if len(sql[table]) > 100000:
            raise DeletionError('inventory_too_large', 'Требуется пакетная обработка большого архива.', 503)
        sql[table].sort(key=dumps)
    legacy = collect_legacy(cursor, columns, student_id, sql, context=legacy_ref_context)
    sql.update(legacy['sql'])
    fk_context = check_fk_scope(cursor, columns, pks, fks, sql)
    # Successful list + find distinguish an absent empty collection from an outage.
    # Unknown/legacy ownership layouts are repair gates, never silent leftovers.
    for table in mongo_db.list_collection_names():
        alternate = [field for field in ('studentId', 'student_id', 'target_student_id') if field != MONGO.get(table)]
        if alternate and mongo_db[table].find_one({'$or': [mongo_filter(student_id, field) for field in alternate]}, {'_id': 1}):
            raise DeletionError('unknown_mongo_ownership', 'Нужна проверка структуры персональных документов MongoDB.', 503)
    mongo = {table: list(mongo_db[table].find(mongo_filter(student_id, field)).limit(100001)) for table, field in MONGO.items()}
    if any(len(values) > 100000 for values in mongo.values()):
        raise DeletionError('inventory_too_large', 'Требуется пакетная обработка большого архива MongoDB.', 503)
    for values in mongo.values():
        values.sort(key=dumps)
    from .retention import collect_retained_fragments
    retained = collect_retained_fragments(cursor, columns, pks, student_id, sql)
    from .commands import collect_command_sanitizations
    commands = collect_command_sanitizations(cursor, columns, student_id, sql)
    counts = summarize(sql, mongo)
    order = deletion_order(set(selected) | set(legacy['sql']), fks)
    manifest = {'sql': {table: [{key: row[key] for key in pks[table]} for row in sql[table]] for table in order},
                'order': order, 'mongo': {table: [str(row['_id']) for row in values] for table, values in mongo.items()},
                'retained': retained['manifest'], 'legacy_ref_context': legacy['context'],
                'command_ids': [action['id'] for action in commands['actions']], 'command_actions': commands['actions'],
                'fk_ref_context': fk_context}
    keys = {str(r['object_key']) for r in sql.get('homework_submission_files', []) if r.get('object_key')}
    keys |= {str(r['staging_key']) for r in sql.get('homework_file_jobs', []) if r.get('staging_key')}
    keys |= {str(r['object_key']) for r in sql.get('homework_object_ownership', []) if r.get('object_key')}
    keys |= {str(r['processed_output_key']) for r in sql.get('homework_file_jobs', []) if r.get('processed_output_key')}
    counts['homework_files'] = len(keys)
    manifest['homework_units'] = len(keys) + 1
    counts['other_records'] += len(retained['manifest']) + len(commands['actions'])
    details = [{'key': table, 'label': LABELS.get(table, table), 'count': len(value)} for table, value in sql.items() if table not in HOMEWORK_OWNED and table not in {'students', 'homework_sessions', 'exam_sessions', 'test_sessions', 'classic_exam_attempts'} and value]
    details += [{'key': 'homework_file_records', 'label': 'PDF-файлы в сдачах', 'count': len(sql.get('homework_submission_files', []))}, {'key': 'homework_upload_jobs', 'label': 'Загрузки и задания обработки PDF', 'count': len(sql.get('homework_file_jobs', []))}, {'key': 'homework_storage_objects', 'label': 'Известные ключи файлов и загрузок (включая историю; без повторов)', 'count': len(keys)}]
    details += [{'key': 'retained_fragments', 'label': 'Персональные строки импортов и квитанций', 'count': len(retained['manifest'])}, {'key': 'rate_rec', 'label': LABELS['rate_rec'], 'count': len(mongo['rate_rec'])}]
    details += [{'key': 'exam_admin_commands', 'label': 'Обезличиваемые персональные квитанции экзаменов', 'count': len(commands['actions'])}]
    return {'counts': counts, 'details': details, 'manifest': manifest, 'fingerprint': digest({'sql': sql, 'mongo': mongo, 'retained': retained['fingerprint'], 'commands': commands['fingerprint'], 'fk_scope': fk_context}), 'sql': sql}
