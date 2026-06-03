-- =============================================================================
-- Школы: справочник + привязка учеников (независимо от groups)
-- =============================================================================

CREATE TABLE IF NOT EXISTS schools (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL COMMENT 'Название школы',
    short_name VARCHAR(64) NULL COMMENT 'Короткое название для таблиц',
    notes TEXT NULL COMMENT 'Комментарий администратора',
    is_active TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 — деактивирована, не для новых учеников',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_schools_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE students
    ADD COLUMN school_id INT NULL COMMENT 'FK schools.id' AFTER group_id,
    ADD INDEX idx_students_school_id (school_id);

-- FK отдельно: на проде колонка может уже существовать без constraint
-- ALTER TABLE students
--     ADD CONSTRAINT fk_students_school
--         FOREIGN KEY (school_id) REFERENCES schools(id)
--         ON DELETE RESTRICT ON UPDATE CASCADE;
