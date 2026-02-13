# cpm-back — единый бэкенд CPM

Объединённый бэкенд: авторизация, логика cpm-serv (домашки, группы, студенты, посещаемость, расписание, заявки на отгул, карточки Platon) и cpm-exam-main (направления, тесты, экзамены, внешние тесты, рейтинги). Один Flask-сервис, MySQL + MongoDB.

## Запуск

```bash
cd cpm-back
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Сервер слушает `0.0.0.0:80` (как в cpm-serv). Для продакшена можно использовать gunicorn:

```bash
gunicorn -w 4 -b 0.0.0.0:80 "cpm_back:create_app()"
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `MYSQL_HOST` | Хост MySQL | 147.45.138.77 |
| `MYSQL_PORT` | Порт MySQL | 3306 |
| `MYSQL_USER` | Пользователь MySQL | minishep |
| `MYSQL_PASSWORD` | Пароль MySQL | — |
| `MYSQL_DATABASE` | База MySQL | minishep |
| `MONGODB_URI` | URI MongoDB | (см. config.py) |
| `MONGODB_DB_NAME` | Имя БД MongoDB | default_db |
| `JWT_SECRET_KEY` | Секрет для JWT | (dev-ключ) |
| `JWT_EXPIRATION_HOURS` | Срок жизни токена (часы) | 24 |
| `FLASK_RUN_HOST` | Хост запуска (если не через main.py) | 0.0.0.0 |
| `FLASK_RUN_PORT` | Порт (в main.py по умолчанию 80) | 80 |
| `FLASK_DEBUG` | Режим отладки | false |

## Структура

- **cpm_back/** — пакет приложения
  - **config.py** — конфигурация из env
  - **db/** — MySQL (пул) и MongoDB
  - **auth/** — авторизация по MySQL, JWT, декораторы ролей
  - **services/serv/** — логика из cpm-serv
  - **services/exam/** — логика из cpm-exam-main + расчёт/сохранение рейтингов
  - **blueprints/** — роуты по модулям (auth, homework, students, groups, attendance, users, schedule, zaps, cards, directions, tests, exams, external_tests, ratings)
- **main.py** — главный файл, точка входа (host 0.0.0.0, port 80)

## API

- **Авторизация:** `POST /api/auth`, `POST /api/logout`, `POST /api/aun`
- **Сервис (cpm-serv):** роуты под префиксом `/api/` (get-homeworks, get-groups, get-students, schedule, create-zap и т.д.)
- **Карточки (Platon):** без префикса `/api/` — `/add-learned-question`, `/get-themes`, `/all-cards-by-theme/...` и т.д.
- **Экзамены/тесты:** без префикса — `/directions`, `/tests/<direction>`, `/test/<test_id>`, `/create-test-session`, `/get-all-exams`, `/get-attendance`, `/external-tests/...`, `/get-all-ratings`, `/get-rating-details`, `/calculate-all-ratings`

Корневой маршрут: `GET /` — проверка работы сервиса.
