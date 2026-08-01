"""
Календарное расписание занятий (MongoDB collection `schedule`).

Одно занятие = одна календарная дата (YYYY-MM-DD).

Поля:
  date, start_time, end_time — дата и время
  lesson_name — предмет
  teacher_name — преподаватель
  location — локация (вуз и т.п.)
  classroom — аудитория (цифры и буквы)
  is_changed — галочка «расписание изменилось»
  is_public / school_id — публичное или внутришкольное

Публичные занятия видны всем; школьные — только студентам своей школы.
"""
import re
from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId

from cpm_back.db.mongo import get_mongo_db, get_mongo_client
from cpm_back.db.mysql_pool import close_db_connection, get_db_connection
from cpm_back.services.serv.school_utils import validate_school_id

DATE_FMT = "%Y-%m-%d"
TIME_FMT = "%H:%M"
# Аудитория: цифры, латиница/кириллица, пробел, дефис (напр. "301А", "А-12")
CLASSROOM_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-\s]*$")


class ScheduleManager:
    def __init__(self):
        self.db = get_mongo_db()
        self.collection = self.db.schedule

    @staticmethod
    def _serialize_lesson(lesson: Dict) -> Dict:
        if "_id" in lesson:
            lesson["_id"] = str(lesson["_id"])
        for key in ("created_at", "updated_at"):
            value = lesson.get(key)
            if isinstance(value, datetime):
                lesson[key] = value.isoformat()
        return lesson

    @staticmethod
    def _parse_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes"):
                return True
            if lowered in ("false", "0", "no"):
                return False
        return default

    @staticmethod
    def _validate_date(value: Any) -> Optional[str]:
        if not value or not isinstance(value, str):
            return None
        try:
            datetime.strptime(value.strip(), DATE_FMT)
            return value.strip()
        except ValueError:
            return None

    @staticmethod
    def _validate_time(value: Any) -> Optional[str]:
        if not value or not isinstance(value, str):
            return None
        raw = value.strip()
        try:
            parsed = datetime.strptime(raw, "%H:%M")
        except ValueError:
            return None
        # Всегда HH:MM — иначе '9:30' > '10:00' лексикографически
        return parsed.strftime(TIME_FMT)

    def _get_student_school_id(self, student_id: Any) -> Optional[int]:
        connection = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT school_id FROM students WHERE id = %s", (student_id,))
            row = cursor.fetchone()
            cursor.close()
            if not row:
                return None
            school_id = row.get("school_id")
            return int(school_id) if school_id is not None else None
        except Exception:
            return None
        finally:
            close_db_connection(connection)

    def _validate_visibility(self, is_public: bool, school_id: Any) -> Dict:
        if is_public:
            if school_id is not None and school_id != "":
                return {
                    "status": False,
                    "error": "Для публичного занятия school_id должен быть пустым",
                }
            return {"status": True, "school_id": None}

        if school_id is None or school_id == "":
            return {
                "status": False,
                "error": "Для школьного занятия укажите school_id",
            }

        connection = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            check = validate_school_id(cursor, school_id)
            cursor.close()
            if not check.get("status"):
                return check
            return {"status": True, "school_id": int(school_id)}
        except Exception as e:
            return {"status": False, "error": f"Ошибка проверки школы: {str(e)}"}
        finally:
            close_db_connection(connection)

    def _find_conflict(
        self,
        *,
        date: str,
        start_time: str,
        end_time: str,
        is_public: bool,
        school_id: Optional[int],
        exclude_id: Optional[ObjectId] = None,
    ) -> Optional[Dict]:
        query: Dict[str, Any] = {
            "date": date,
            "start_time": {"$lt": end_time},
            "end_time": {"$gt": start_time},
        }
        if is_public:
            query["is_public"] = True
        else:
            query["is_public"] = False
            query["school_id"] = school_id

        if exclude_id is not None:
            query["_id"] = {"$ne": exclude_id}

        return self.collection.find_one(query)

    def _normalize_lesson_payload(self, lesson_data: Dict) -> Dict:
        date = self._validate_date(lesson_data.get("date"))
        if not date:
            return {"status": False, "error": "Поле 'date' обязательно (формат YYYY-MM-DD)"}

        start_time = self._validate_time(lesson_data.get("start_time"))
        end_time = self._validate_time(lesson_data.get("end_time"))
        if not start_time:
            return {"status": False, "error": "Поле 'start_time' обязательно (формат HH:MM)"}
        if not end_time:
            return {"status": False, "error": "Поле 'end_time' обязательно (формат HH:MM)"}
        if start_time >= end_time:
            return {"status": False, "error": "Время окончания должно быть больше времени начала"}

        required_strings = ("lesson_name", "teacher_name", "location", "classroom")
        for field in required_strings:
            value = lesson_data.get(field)
            if not value or not isinstance(value, str) or not value.strip():
                return {"status": False, "error": f"Поле '{field}' обязательно для заполнения"}

        classroom = lesson_data["classroom"].strip()
        if not CLASSROOM_RE.match(classroom):
            return {
                "status": False,
                "error": "Поле 'classroom' должно содержать цифры и буквы (допустим дефис/пробел)",
            }

        is_public = self._parse_bool(lesson_data.get("is_public"), default=True)
        visibility = self._validate_visibility(is_public, lesson_data.get("school_id"))
        if not visibility.get("status"):
            return visibility

        is_changed = self._parse_bool(lesson_data.get("is_changed"), default=False)

        return {
            "status": True,
            "payload": {
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "lesson_name": lesson_data["lesson_name"].strip(),
                "teacher_name": lesson_data["teacher_name"].strip(),
                "location": lesson_data["location"].strip(),
                "classroom": classroom,
                "is_changed": is_changed,
                "is_public": is_public,
                "school_id": visibility["school_id"],
            },
        }

    def get_schedule(
        self,
        *,
        date_from: str,
        date_to: str,
        role: str,
        user_id: Any = None,
    ) -> Dict:
        try:
            get_mongo_client().admin.command("ping")

            parsed_from = self._validate_date(date_from)
            parsed_to = self._validate_date(date_to)
            if not parsed_from or not parsed_to:
                return {
                    "status": False,
                    "error": "Параметры date_from и date_to обязательны (формат YYYY-MM-DD)",
                }
            if parsed_from > parsed_to:
                return {"status": False, "error": "date_from не может быть больше date_to"}

            query: Dict[str, Any] = {"date": {"$gte": parsed_from, "$lte": parsed_to}}

            if role == "student":
                student_school_id = self._get_student_school_id(user_id)
                if student_school_id is None:
                    query["is_public"] = True
                else:
                    query["$or"] = [
                        {"is_public": True},
                        {"is_public": False, "school_id": student_school_id},
                    ]

            schedule = list(self.collection.find(query).sort([("date", 1), ("start_time", 1)]))
            for lesson in schedule:
                self._serialize_lesson(lesson)

            return {
                "status": True,
                "message": "Расписание успешно загружено",
                "schedule": schedule,
            }
        except Exception as e:
            return {"status": False, "error": f"Ошибка при загрузке расписания: {str(e)}"}

    def get_all_schedule(self) -> Dict:
        """Обратная совместимость: без диапазона дат возвращает ошибку с подсказкой."""
        return {
            "status": False,
            "error": "Укажите date_from и date_to (YYYY-MM-DD)",
        }

    def add_lesson(self, lesson_data: Dict) -> Dict:
        try:
            get_mongo_client().admin.command("ping")
            normalized = self._normalize_lesson_payload(lesson_data or {})
            if not normalized.get("status"):
                return normalized

            payload = normalized["payload"]
            conflict = self._find_conflict(
                date=payload["date"],
                start_time=payload["start_time"],
                end_time=payload["end_time"],
                is_public=payload["is_public"],
                school_id=payload["school_id"],
            )
            if conflict:
                return {
                    "status": False,
                    "error": f"Занятие пересекается с существующим: {conflict.get('lesson_name', '')}",
                }

            now = datetime.now()
            payload["created_at"] = now
            payload["updated_at"] = now
            result = self.collection.insert_one(payload)
            return {
                "status": True,
                "message": "Занятие успешно добавлено",
                "lesson_id": str(result.inserted_id),
            }
        except Exception as e:
            return {"status": False, "error": str(e)}

    def edit_lesson(self, lesson_id: str, lesson_data: Dict) -> Dict:
        try:
            get_mongo_client().admin.command("ping")
            if not ObjectId.is_valid(lesson_id):
                return {"status": False, "error": "Некорректный ID занятия"}

            object_id = ObjectId(lesson_id)
            existing = self.collection.find_one({"_id": object_id})
            if not existing:
                return {"status": False, "error": "Занятие не найдено"}

            normalized = self._normalize_lesson_payload(lesson_data or {})
            if not normalized.get("status"):
                return normalized

            payload = normalized["payload"]
            conflict = self._find_conflict(
                date=payload["date"],
                start_time=payload["start_time"],
                end_time=payload["end_time"],
                is_public=payload["is_public"],
                school_id=payload["school_id"],
                exclude_id=object_id,
            )
            if conflict:
                return {
                    "status": False,
                    "error": f"Занятие пересекается с существующим: {conflict.get('lesson_name', '')}",
                }

            payload["updated_at"] = datetime.now()
            result = self.collection.update_one({"_id": object_id}, {"$set": payload})
            if result.matched_count == 0:
                return {"status": False, "error": "Занятие не найдено"}
            return {"status": True, "message": "Занятие успешно обновлено"}
        except Exception as e:
            return {"status": False, "error": str(e)}

    def delete_lesson(self, lesson_id: str) -> Dict:
        try:
            if not ObjectId.is_valid(lesson_id):
                return {"status": False, "error": "Некорректный ID занятия"}
            result = self.collection.delete_one({"_id": ObjectId(lesson_id)})
            return {
                "status": result.deleted_count > 0,
                "message": "Занятие успешно удалено" if result.deleted_count else "Занятие не найдено",
            }
        except Exception as e:
            return {"status": False, "error": str(e)}

    def close_connection(self):
        pass  # shared client, no per-instance close
