-- Индексы для админки групп (фильтр/поиск по group_id, name)
-- Безопасно запускать повторно: при дубликате индекса MySQL вернёт ошибку — пропустить.

ALTER TABLE students ADD INDEX idx_students_group_id (group_id);
ALTER TABLE proctors ADD INDEX idx_proctors_group_id (group_id);
ALTER TABLE students ADD INDEX idx_students_full_name (full_name);
