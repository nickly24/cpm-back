from datetime import datetime
from typing import Dict

from cpm_back.db.mongo import get_mongo_db, get_mongo_client


class ScheduleManager:
    def __init__(self):
        self.db = get_mongo_db()
        self.collection = self.db.schedule

    def get_all_schedule(self) -> Dict:
        try:
            get_mongo_client().admin.command('ping')
            schedule = list(self.collection.find())
            for lesson in schedule:
                if '_id' in lesson:
                    lesson['_id'] = str(lesson['_id'])
            return {
                "status": True,
                "message": "Расписание успешно загружено",
                "schedule": schedule
            }
        except Exception as e:
            return {"status": False, "error": f"Ошибка при загрузке расписания: {str(e)}"}

    def add_lesson(self, lesson_data: Dict) -> Dict:
        try:
            get_mongo_client().admin.command('ping')
            required_fields = ['day_of_week', 'start_time', 'end_time', 'lesson_name', 'teacher_name', 'location']
            for field in required_fields:
                if not lesson_data.get(field):
                    return {"status": False, "error": f"Поле '{field}' обязательно для заполнения"}
            valid_days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            if lesson_data['day_of_week'] not in valid_days:
                return {"status": False, "error": "Некорректный день недели"}
            if lesson_data['start_time'] >= lesson_data['end_time']:
                return {"status": False, "error": "Время окончания должно быть больше времени начала"}
            existing_lesson = self.collection.find_one({
                "day_of_week": lesson_data['day_of_week'],
                "$or": [
                    {"start_time": {"$lt": lesson_data['end_time']}, "end_time": {"$gt": lesson_data['start_time']}}
                ]
            })
            if existing_lesson:
                return {"status": False, "error": f"Занятие пересекается с существующим: {existing_lesson.get('lesson_name', '')}"}
            lesson_data['created_at'] = datetime.now()
            lesson_data['updated_at'] = datetime.now()
            result = self.collection.insert_one(lesson_data)
            return {"status": True, "message": "Занятие успешно добавлено", "lesson_id": str(result.inserted_id)}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def edit_lesson(self, lesson_id: str, lesson_data: Dict) -> Dict:
        try:
            from bson import ObjectId
            get_mongo_client().admin.command('ping')
            if not ObjectId.is_valid(lesson_id):
                return {"status": False, "error": "Некорректный ID занятия"}
            existing_lesson = self.collection.find_one({"_id": ObjectId(lesson_id)})
            if not existing_lesson:
                return {"status": False, "error": "Занятие не найдено"}
            required_fields = ['day_of_week', 'start_time', 'end_time', 'lesson_name', 'teacher_name', 'location']
            for field in required_fields:
                if not lesson_data.get(field):
                    return {"status": False, "error": f"Поле '{field}' обязательно"}
            valid_days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            if lesson_data['day_of_week'] not in valid_days:
                return {"status": False, "error": "Некорректный день недели"}
            if lesson_data['start_time'] >= lesson_data['end_time']:
                return {"status": False, "error": "Время окончания должно быть больше времени начала"}
            conflicting_lesson = self.collection.find_one({
                "_id": {"$ne": ObjectId(lesson_id)},
                "day_of_week": lesson_data['day_of_week'],
                "$or": [
                    {"start_time": {"$lt": lesson_data['end_time']}, "end_time": {"$gt": lesson_data['start_time']}}
                ]
            })
            if conflicting_lesson:
                return {"status": False, "error": "Занятие пересекается с существующим"}
            lesson_data['updated_at'] = datetime.now()
            result = self.collection.update_one({"_id": ObjectId(lesson_id)}, {"$set": lesson_data})
            return {"status": result.modified_count > 0, "message": "Занятие успешно обновлено" if result.modified_count else "Не удалось обновить"}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def delete_lesson(self, lesson_id: str) -> Dict:
        try:
            from bson import ObjectId
            if not ObjectId.is_valid(lesson_id):
                return {"status": False, "error": "Некорректный ID занятия"}
            result = self.collection.delete_one({"_id": ObjectId(lesson_id)})
            return {"status": result.deleted_count > 0, "message": "Занятие успешно удалено" if result.deleted_count else "Занятие не найдено"}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def close_connection(self):
        pass  # shared client, no per-instance close
