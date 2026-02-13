"""
Сохранение рейтингов в MySQL (Allratings) и MongoDB (rate_rec).
Принимает mysql_conn и mongo_db из пула приложения.
"""
from datetime import datetime


def save_rating_to_mysql(mysql_conn, rating_data):
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM Allratings WHERE student_id = %s", (rating_data['student_id'],))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE Allratings SET exams = %s, homework = %s, tests = %s, final = %s WHERE student_id = %s
            """, (
                float(rating_data['exams']['rating']), float(rating_data['homework']['rating']),
                float(rating_data['tests']['rating']), float(rating_data['final_rating']),
                rating_data['student_id']
            ))
            rating_id = existing['id']
            mysql_conn.commit()
            return {'success': True, 'rating_id': rating_id, 'message': 'Рейтинг обновлен', 'is_new': False}
        cursor.execute("""
            INSERT INTO Allratings (student_id, exams, homework, tests, final)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            rating_data['student_id'],
            float(rating_data['exams']['rating']), float(rating_data['homework']['rating']),
            float(rating_data['tests']['rating']), float(rating_data['final_rating'])
        ))
        rating_id = cursor.lastrowid
        mysql_conn.commit()
        return {'success': True, 'rating_id': rating_id, 'message': 'Рейтинг создан', 'is_new': True}
    except Exception as e:
        mysql_conn.rollback()
        return {'success': False, 'rating_id': None, 'message': str(e), 'is_new': False}
    finally:
        cursor.close()


def save_rating_details_to_mongo(mongo_db, rating_id, rating_data):
    rate_rec_collection = mongo_db.rate_rec
    try:
        mongo_doc = {
            'rating_id': rating_id, 'student_id': rating_data['student_id'],
            'date_from': rating_data['date_from'], 'date_to': rating_data['date_to'],
            'calculated_at': datetime.utcnow().isoformat() + 'Z',
            'homework': {'rating': rating_data['homework']['rating'], 'details': rating_data['homework']['details']},
            'exams': {'rating': rating_data['exams']['rating'], 'details': rating_data['exams']['details']},
            'tests': {'rating': rating_data['tests']['rating'], 'directions': rating_data['tests']['directions'], 'details': rating_data['tests']['details']},
            'final_rating': rating_data['final_rating']
        }
        existing = rate_rec_collection.find_one({'rating_id': rating_id})
        if existing:
            rate_rec_collection.update_one({'rating_id': rating_id}, {'$set': mongo_doc})
            return {'success': True, 'mongo_id': str(existing['_id']), 'message': 'Детализация обновлена', 'is_new': False}
        result = rate_rec_collection.insert_one(mongo_doc)
        return {'success': True, 'mongo_id': str(result.inserted_id), 'message': 'Детализация создана', 'is_new': True}
    except Exception as e:
        return {'success': False, 'mongo_id': None, 'message': str(e), 'is_new': False}


def clear_all_ratings(mysql_conn, mongo_db):
    mysql_success, mongo_success = False, False
    mysql_error, mongo_error = None, None
    try:
        cursor = mysql_conn.cursor()
        cursor.execute("DELETE FROM Allratings")
        mysql_conn.commit()
        cursor.close()
        mysql_success = True
    except Exception as e:
        mysql_conn.rollback()
        mysql_error = str(e)
    try:
        mongo_db.rate_rec.delete_many({})
        mongo_success = True
    except Exception as e:
        mongo_error = str(e)
    return {'mysql_success': mysql_success, 'mongo_success': mongo_success, 'mysql_error': mysql_error, 'mongo_error': mongo_error}


def check_student_exists(mysql_conn, student_id):
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
    result = cursor.fetchone()
    cursor.close()
    return result is not None


def save_all_ratings(mysql_conn, mongo_db, date_from, date_to):
    from .calculate_ratings import calculate_student_rating
    clear_result = clear_all_ratings(mysql_conn, mongo_db)
    if not clear_result['mysql_success']:
        return {'total_students': 0, 'successful': 0, 'failed': 0, 'errors': [f"Ошибка очистки MySQL: {clear_result['mysql_error']}"], 'clear_error': True}
    if not clear_result['mongo_success']:
        return {'total_students': 0, 'successful': 0, 'failed': 0, 'errors': [f"Ошибка очистки MongoDB: {clear_result['mongo_error']}"], 'clear_error': True}
    cursor = mysql_conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM students ORDER BY id")
    students = cursor.fetchall()
    cursor.close()
    results = {'total_students': len(students), 'successful': 0, 'failed': 0, 'errors': [], 'skipped': 0}
    for student in students:
        student_id = student['id']
        if not check_student_exists(mysql_conn, student_id):
            results['skipped'] += 1
            results['errors'].append({'student_id': student_id, 'error': 'Студент не найден'})
            continue
        try:
            rating_data = calculate_student_rating(mysql_conn, mongo_db, student_id, date_from, date_to)
            mysql_result = save_rating_to_mysql(mysql_conn, rating_data)
            if mysql_result['success']:
                mongo_result = save_rating_details_to_mongo(mongo_db, mysql_result['rating_id'], rating_data)
                if mongo_result['success']:
                    results['successful'] += 1
                else:
                    results['failed'] += 1
                    results['errors'].append({'student_id': student_id, 'error': mongo_result['message']})
            else:
                results['failed'] += 1
                results['errors'].append({'student_id': student_id, 'error': mysql_result['message']})
        except Exception as e:
            results['failed'] += 1
            results['errors'].append({'student_id': student_id, 'error': str(e)})
    return results
