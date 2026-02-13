"""
Расчёт рейтинга студентов: ДЗ, экзамены, тесты (MySQL + MongoDB).
Использует переданные mysql_conn и mongo_db (пул/клиент приложения).
"""
from datetime import datetime


def calculate_homework_rating(mysql_conn, student_id, date_from, date_to):
    cursor = mysql_conn.cursor(dictionary=True)
    query = """
        SELECT h.id, h.name, h.type, h.deadline, hs.result, hs.status, hs.date_pass
        FROM homework h
        LEFT JOIN homework_sessions hs ON h.id = hs.homework_id AND hs.student_id = %s
        WHERE h.type = 'ОВ' AND h.deadline >= %s AND h.deadline <= %s AND h.deadline < CURDATE()
        ORDER BY h.deadline DESC
    """
    cursor.execute(query, (student_id, date_from, date_to))
    homeworks = cursor.fetchall()
    cursor.close()
    total_score = 0
    completed_count = 0
    details = []
    for hw in homeworks:
        status, score = "Не сдано", 0
        if hw['status'] == 1 and hw['result'] is not None:
            status, score = "Сдано", float(hw['result'])
            completed_count += 1
        total_score += score
        details.append({
            'homework_id': hw['id'], 'name': hw['name'],
            'deadline': str(hw['deadline']) if hw['deadline'] else None,
            'score': score, 'status': status,
            'date_pass': str(hw['date_pass']) if hw['date_pass'] else None
        })
    average_score = total_score / len(homeworks) if homeworks else 0
    return {
        'average': average_score, 'total_count': len(homeworks),
        'completed_count': completed_count, 'total_score': total_score, 'details': details
    }


def calculate_exams_rating(mysql_conn, student_id, date_from, date_to):
    cursor = mysql_conn.cursor(dictionary=True)
    query = """
        SELECT es.id, es.points as score, e.id as exam_id, e.name as exam_name, e.date as exam_date
        FROM exams e
        LEFT JOIN exam_sessions es ON es.exam_id = e.id AND es.student_id = %s
        WHERE e.date >= %s AND e.date <= %s
        ORDER BY e.date DESC
    """
    cursor.execute(query, (student_id, date_from, date_to))
    exams = cursor.fetchall()
    cursor.close()
    total_score = 0
    details = []
    for exam in exams:
        score = float(exam['score']) if exam['score'] is not None else 0
        total_score += score
        details.append({
            'exam_id': exam['exam_id'], 'exam_name': exam['exam_name'],
            'exam_date': str(exam['exam_date']) if exam['exam_date'] else None,
            'score': score, 'status': 'Сдан' if exam['score'] is not None else 'Не сдавал'
        })
    average_score = total_score / len(exams) if exams else 0
    return {'average': average_score, 'total_count': len(exams), 'total_score': total_score, 'details': details}


def calculate_tests_rating(mysql_conn, mongo_db, student_id, date_from, date_to):
    tests_collection = mongo_db.tests
    test_sessions_collection = mongo_db.test_sessions
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM directions")
    directions = {d['id']: d['name'] for d in cursor.fetchall()}
    cursor.close()
    student_sessions = {}
    for session in test_sessions_collection.find(
        {"studentId": str(student_id)}, {"testId": 1, "score": 1}
    ):
        student_sessions[session['testId']] = session.get('score', 0)
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.id, t.name, t.direction_id, t.date, ts.rate
        FROM tests_out t
        LEFT JOIN test_sessions ts ON t.id = ts.test_id AND ts.student_id = %s
        WHERE t.date >= %s AND t.date <= %s
        ORDER BY t.date DESC
    """, (student_id, date_from, date_to))
    external_tests = cursor.fetchall()
    cursor.close()
    date_from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    date_to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    all_mongo_tests = list(tests_collection.find({}))
    mongo_tests = []
    for test in all_mongo_tests:
        start_date_str = test.get('startDate', '')
        if start_date_str:
            try:
                if 'T' in start_date_str:
                    start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00').replace('+00:00', ''))
                else:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                if date_from_dt <= start_date <= date_to_dt:
                    mongo_tests.append(test)
            except Exception:
                pass
    directions_dict = {}
    for test in mongo_tests:
        direction = test.get('direction', 'Неизвестное направление')
        test_id = str(test['_id'])
        test_title = test.get('title', 'Без названия')
        score = student_sessions.get(test_id, 0)
        if direction not in directions_dict:
            directions_dict[direction] = {'tests': [], 'scores': []}
        directions_dict[direction]['tests'].append({'test_id': test_id, 'title': test_title, 'score': score, 'source': 'MongoDB'})
        directions_dict[direction]['scores'].append(score)
    for test in external_tests:
        direction_id = test.get('direction_id')
        direction_name = directions.get(direction_id, f'Направление ID {direction_id}')
        test_id = f"external_{test['id']}"
        test_title = test.get('name', 'Без названия')
        score = float(test.get('rate', 0)) if test.get('rate') is not None else 0
        if direction_name not in directions_dict:
            directions_dict[direction_name] = {'tests': [], 'scores': []}
        directions_dict[direction_name]['tests'].append({'test_id': test_id, 'title': test_title, 'score': score, 'source': 'MySQL (внешний)'})
        directions_dict[direction_name]['scores'].append(score)
    direction_averages = {}
    all_tests_details = []
    for direction, data in directions_dict.items():
        tests_count = len(data['scores'])
        total_score = sum(data['scores'])
        avg_score = total_score / tests_count if tests_count > 0 else 0
        direction_averages[direction] = avg_score
        for test_info in data['tests']:
            all_tests_details.append({
                'direction': direction, 'test_id': test_info['test_id'],
                'title': test_info['title'], 'score': test_info['score'], 'source': test_info['source']
            })
    overall_average = sum(direction_averages.values()) / len(direction_averages) if direction_averages else 0
    total_tests_count = sum(len(data['scores']) for data in directions_dict.values())
    return {
        'average': overall_average, 'total_count': total_tests_count,
        'directions': direction_averages, 'details': all_tests_details
    }


def calculate_final_rating(hw_rating, exams_rating, tests_rating):
    hw_component = (hw_rating['average'] * 25) / 100
    exams_component = exams_rating['average'] * 6
    tests_component = (tests_rating['average'] * 45) / 100
    return hw_component + exams_component + tests_component


def calculate_student_rating(mysql_conn, mongo_db, student_id, date_from, date_to):
    hw_rating = calculate_homework_rating(mysql_conn, student_id, date_from, date_to)
    exams_rating = calculate_exams_rating(mysql_conn, student_id, date_from, date_to)
    tests_rating = calculate_tests_rating(mysql_conn, mongo_db, student_id, date_from, date_to)
    final_rating = calculate_final_rating(hw_rating, exams_rating, tests_rating)
    return {
        'student_id': student_id, 'date_from': date_from, 'date_to': date_to,
        'homework': {'rating': hw_rating['average'], 'details': hw_rating['details']},
        'exams': {'rating': exams_rating['average'], 'details': exams_rating['details']},
        'tests': {'rating': tests_rating['average'], 'details': tests_rating['details'], 'directions': tests_rating['directions']},
        'final_rating': final_rating
    }
