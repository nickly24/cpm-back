"""Explicit section policy for delegated administrators. Unlisted operations are denied.

Existing admin/proctor/student checks remain in their handlers. A staff_admin is admitted
by those decorators only after this policy has authorised the actual endpoint and payload.
"""
from flask import g, jsonify, request

SECTIONS = {
    'dashboard': 'Главная', 'users': 'Пользователи', 'schools': 'Школы',
    'upload': 'Загрузка', 'schedule': 'Расписание', 'telegram-bot': 'Telegram-бот',
    'assignments': 'Домашние задания', 'review-queue': 'Очередь работ',
    'homework-archive': 'Архив работ', 'monitoring': 'Мониторинг',
    'tests': 'Тесты', 'test-results': 'Результаты', 'exams': 'Экзамены',
    'attendance': 'Посещаемость', 'scan': 'Сканирование',
    'zaps': 'Запросы на отгул', 'train': 'Карточки', 'ratings': 'Рейтинг',
}


def has_permission(user, section, action='view'):
    if not user:
        return False
    if user.get('role') == 'admin':
        return True
    if user.get('role') != 'staff_admin' or section not in SECTIONS:
        return False
    flags = (user.get('permissions') or {}).get(section) or {}
    return flags.get('edit') is True if action == 'edit' else flags.get('view') is True or flags.get('edit') is True


RULES = {}


def rules(namespace, section, view='', edit=''):
    for action, names in (('view', view), ('edit', edit)):
        for name in names.split():
            RULES[f'{namespace}.{name}'] = ((section, action),)


rules('students', 'users', 'group_filter list_students by_id', 'add edit')
rules('users', 'users', 'by_role', 'delete add_staff edit_staff reset_staff')
rules('groups', 'users', 'groups_students groups_overview group_members groups_search list_groups unsigned',
      'create_group update_group remove_group remove_student remove_proctor change_proctor change_student')
rules('schools', 'schools', 'list_schools school_by_id school_filter unsigned_students', 'create_school update_school')
rules('schedule', 'schedule', 'get_schedule', 'add_lesson bulk_save_schedule edit_lesson delete_lesson')
rules('telegram_bot', 'telegram-bot', 'status settings', 'update_settings start stop restart')
rules('homework', 'assignments',
      'list_homeworks proctor_sessions all_results results_paginated homework_students homework_detail homework_overview homework_students_get ov_table',
      'pass_hw pass_hw_bulk edit_session homework_update homework_toggle_published homework_delete_rest create_hw delete_hw')
rules('tests', 'tests',
      'tests_by_direction test_by_id admin_test_overview admin_sessions_by_test sessions_by_test admin_attempts_by_test admin_test_changes admin_test_question_changes admin_tests_changes_recent admin_session_detail get_session sessions_by_student session_stats session_by_student_and_test session_review',
      'create update delete toggle_visibility toggle_published create_session admin_delete_session')
rules('test_attempts', 'tests', 'admin_attempt_detail', 'admin_delete_attempt admin_force_submit_attempt')
rules('test_drafts', 'tests', 'drafts_list drafts_get',
      'drafts_create drafts_from_test drafts_update drafts_delete drafts_lock drafts_unlock drafts_publish')
rules('external_tests', 'tests', 'list_all by_direction for_student delete_preview', 'create delete')
rules('exams', 'exams', 'list_exams exam_session student_sessions all_sessions sessions_by_exam delete_preview', 'delete')
rules('exams', 'attendance', 'attendance')
rules('attendance', 'attendance', 'by_date by_month', 'add')
rules('class_days', 'attendance', 'attendance_types_list attendance_report class_days_list class_days_get class_day_attendance_list student_class_day_attendance',
      'class_days_create class_days_update class_days_delete class_day_attendance_set class_day_attendance_delete')
rules('zaps', 'zaps', 'by_student all_zaps by_id', 'create process retry_zap_date_route unlink_zap_date_route')
rules('cards', 'train', 'get_training_tree_route direction_sections_route section_study_view_route section_batch_route get_admin_training_catalog_route admin_cards_by_theme_route',
      'section_study_settings_route mark_card_learned_route unmark_card_learned_route create_training_theme_route update_training_theme_route delete_training_theme_route create_card_route update_card_route delete_card_route create_theme_with_questions_route')
rules('card_transform', 'train', 'session_detail', 'sessions_create session_commit')
rules('ratings', 'ratings', 'ratings_report all_ratings all_ratings_legacy rating_details rating_recalc_jobs_list rating_recalc_job_detail student_rating', 'calculate_all')
rules('user_import', 'upload', 'session_detail jobs_list job_detail job_report', 'parse_upload session_update session_commit')
rules('card_import', 'upload', 'session_detail', 'parse_upload session_update session_commit')
rules('external_test_results_import', 'upload', 'session_detail', 'parse_upload session_update session_commit')
rules('test_import', 'upload', '', 'preview_test_import commit_test_import')


def shared(endpoint, sections, action='view'):
    RULES[endpoint] = tuple((section, action) for section in sections.split())


# Read dependencies of existing screens. These do not grant directory-editing rights.
shared('students.list_students', 'users schools test-results attendance scan ratings zaps')
shared('students.by_id', 'users attendance scan zaps')
shared('students.group_filter', 'users assignments attendance ratings')
shared('groups.list_groups', 'users schools assignments attendance ratings schedule exams test-results')
shared('groups.groups_students', 'users attendance ratings')
shared('groups.groups_search', 'users schools')
shared('schools.list_schools', 'schools users schedule attendance ratings upload')
shared('directions.list_directions', 'tests train upload ratings exams')
shared('external_tests.list_all', 'tests upload')
shared('class_days.class_days_list', 'attendance scan')
shared('class_days.class_days_get', 'attendance scan')
shared('class_days.class_day_attendance_list', 'attendance scan')
shared('class_days.attendance_types_list', 'attendance scan')
shared('zaps.by_id', 'zaps attendance')
shared('zaps.unlink_zap_date_route', 'zaps attendance', 'edit')
shared('cards.get_admin_training_catalog_route', 'train upload')
shared('homework.list_homeworks', 'assignments homework-archive review-queue')
for name in 'get_session sessions_by_student session_stats session_by_student_and_test session_review admin_session_detail'.split():
    shared(f'tests.{name}', 'tests test-results')


def requirements(endpoint, payload=None):
    payload = payload if isinstance(payload, dict) else {}
    if endpoint == 'students.edit':
        if set(payload) <= {'student_id', 'school_id'} and 'school_id' in payload:
            return (('users', 'edit'), ('schools', 'edit'))
    if endpoint == 'class_days.class_day_attendance_set':
        # A scanner may only mark in-person attendance, never create arbitrary statuses/zap links.
        if set(payload) <= {'student_id', 'attendance_type_id'} and payload.get('attendance_type_id') == 1:
            return (('attendance', 'edit'), ('scan', 'edit'))
    return RULES.get(endpoint, ())


def allowed(user, endpoint, payload=None):
    if endpoint.startswith('users.') and isinstance(payload, dict):
        if payload.get('role') not in {'student', 'proctor', 'examinator', 'supervisor'}:
            return False
    return any(has_permission(user, section, action) for section, action in requirements(endpoint, payload))


def enforce_admin_permissions():
    from .jwt_auth import get_current_user
    if request.method == 'OPTIONS' or request.endpoint in {'auth.login', 'auth.logout'}:
        return None
    user = get_current_user()
    if not user or user.get('role') != 'staff_admin':
        return None
    if request.endpoint == 'auth.aun':
        return None
    if not allowed(user, request.endpoint or '', request.get_json(silent=True)):
        return jsonify({'status': False, 'error': 'Недостаточно прав для этого действия'}), 403
    g.staff_admin_authorized = True
    return None


def delegated_role_allowed(user, allowed_roles):
    return (user and user.get('role') == 'staff_admin' and 'admin' in allowed_roles
            and getattr(g, 'staff_admin_authorized', False))


def redact_directory_response(response):
    from .jwt_auth import get_current_user
    user = get_current_user()
    if not user or user.get('role') != 'staff_admin' or not response.is_json:
        return response
    data = response.get_json()
    if request.endpoint == 'telegram_bot.settings':
        if not has_permission(user, 'telegram-bot', 'edit') and isinstance(data, dict):
            settings = data.get('settings')
            if isinstance(settings, dict):
                settings['bot_token'] = ''
    elif request.endpoint in {'students.list_students', 'students.by_id', 'students.group_filter', 'groups.groups_search', 'users.by_role'}:
        directory_view = has_permission(user, 'users')
        if has_permission(user, 'users', 'edit'):
            return response

        def redact(value):
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                hidden = {'password'} if directory_view else {'password', 'login', 'tg_name', 'password_hidden'}
                result = {k: redact(v) for k, v in value.items() if k not in hidden}
                if directory_view and 'password' in value:
                    result['password_hidden'] = True
                return result
            return value
        data = redact(data)
    else:
        return response
    response.set_data(__import__('json').dumps(data, ensure_ascii=False))
    return response
