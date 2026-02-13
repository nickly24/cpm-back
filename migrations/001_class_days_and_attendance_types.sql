-- =============================================================================
-- Новая модель посещаемости: дни занятий + привязка посещаемости к ним
-- Старые таблицы (attendance, zaps) не трогаем — данные пригодятся для миграции.
-- =============================================================================

-- Справочник типов посещения (1–8)
CREATE TABLE IF NOT EXISTS attendance_types (
    id TINYINT UNSIGNED PRIMARY KEY,
    code VARCHAR(50) NOT NULL COMMENT 'Код для API/логики',
    name_ru VARCHAR(255) NOT NULL COMMENT 'Название для отображения',
    sort_order TINYINT UNSIGNED NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO attendance_types (id, code, name_ru, sort_order) VALUES
(1, 'in_person', 'Очное присутствие', 1),
(2, 'absent_valid', 'Отсутствие по уважительной причине', 2),
(3, 'remote_permanent', 'Дистанционное присутствие постоянное', 3),
(4, 'remote_scheduled', 'Дистанционное присутствие по расписанию', 4),
(5, 'remote_valid', 'Дистанционное присутствие по уважительной причине', 5),
(6, 'late', 'Опоздание', 6),
(7, 'joined_later', 'Присоединение после начала обучения в Сборной', 7),
(8, 'hybrid', 'Очное дистанционное присутствие', 8)
ON DUPLICATE KEY UPDATE code = VALUES(code), name_ru = VALUES(name_ru), sort_order = VALUES(sort_order);

-- День занятий (лист посещаемости): одна запись = одно занятие в календаре
CREATE TABLE IF NOT EXISTS class_days (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    date DATE NOT NULL COMMENT 'Дата занятия',
    comment VARCHAR(500) NULL COMMENT 'Комментарий (опционально)',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_class_days_date (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Посещаемость: привязка студента к дню занятий с типом посещения
CREATE TABLE IF NOT EXISTS class_day_attendance (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    class_day_id INT UNSIGNED NOT NULL,
    student_id INT NOT NULL,
    attendance_type_id TINYINT UNSIGNED NOT NULL,
    zap_id INT UNSIGNED NULL COMMENT 'Связь с отгулом для типов «по уважительной причине»',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_class_day_student (class_day_id, student_id),
    CONSTRAINT fk_cda_class_day FOREIGN KEY (class_day_id) REFERENCES class_days(id) ON DELETE CASCADE,
    CONSTRAINT fk_cda_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    CONSTRAINT fk_cda_type FOREIGN KEY (attendance_type_id) REFERENCES attendance_types(id),
    CONSTRAINT fk_cda_zap FOREIGN KEY (zap_id) REFERENCES zaps(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Если таблицы zaps ещё нет — выполните вариант без FK и добавьте колонку вручную:
-- ALTER TABLE class_day_attendance ADD COLUMN zap_id INT UNSIGNED NULL AFTER attendance_type_id;
-- (FK fk_cda_zap тогда не создавать)

-- Индексы для выборок
CREATE INDEX idx_class_days_date ON class_days(date);
CREATE INDEX idx_class_day_attendance_student ON class_day_attendance(student_id);
CREATE INDEX idx_class_day_attendance_class_day ON class_day_attendance(class_day_id);
