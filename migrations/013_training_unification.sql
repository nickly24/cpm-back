-- =============================================================================
-- Тренировки v2: directions вместо training_sections, новый прогресс и настройки
-- =============================================================================

-- Очистка legacy-данных (контент признан мусорным перед переездом)
DELETE FROM student_progress;
DELETE FROM cards;
DELETE FROM card_themes;

-- Новые таблицы прогресса и настроек заучивания
CREATE TABLE IF NOT EXISTS student_card_progress (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    section_kind ENUM('manual', 'test') NOT NULL,
    section_ref_id VARCHAR(24) NOT NULL,
    card_ref VARCHAR(64) NOT NULL,
    content_fingerprint CHAR(64) NOT NULL,
    learned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_card_ref (student_id, card_ref),
    INDEX idx_scp_student_section (student_id, section_kind, section_ref_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS student_section_study_settings (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    section_kind ENUM('manual', 'test') NOT NULL,
    section_ref_id VARCHAR(24) NOT NULL,
    batch_size INT NOT NULL DEFAULT 10,
    last_batch_index INT NULL,
    study_mode ENUM('all', 'unlearned', 'learned', 'stale') NOT NULL DEFAULT 'unlearned',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_section_settings (student_id, section_kind, section_ref_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- cards: порядок для батчей
ALTER TABLE cards
    ADD COLUMN sort_order INT NOT NULL DEFAULT 0 COMMENT 'Порядок в разделе' AFTER theme_id;

-- card_themes: section_id -> direction_id
ALTER TABLE card_themes
    DROP FOREIGN KEY fk_card_themes_section;

ALTER TABLE card_themes
    DROP INDEX idx_card_themes_section;

ALTER TABLE card_themes
    DROP COLUMN section_id;

ALTER TABLE card_themes
    ADD COLUMN direction_id INT NOT NULL COMMENT 'FK directions.id' AFTER name,
    ADD INDEX idx_card_themes_direction_id (direction_id);

-- training_sections больше не нужна
DROP TABLE IF EXISTS training_sections;
