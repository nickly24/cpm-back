# Миграция 003: видимость домашних заданий

Выполнить на MySQL перед деплоем бэкенда:

```bash
mysql -u USER -p DATABASE < migrations/003_homework_published.sql
```

Добавляет колонку `homework.published` (1 = студенты видят задание, 0 = скрыто).
