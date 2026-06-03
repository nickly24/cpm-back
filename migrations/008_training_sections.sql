-- =============================================================================
-- Иерархия тренировок: разделы (training_sections) → темы (card_themes)
-- =============================================================================

CREATE TABLE IF NOT EXISTS training_sections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL COMMENT 'Тема предмета (раздел)',
    sort_order INT NOT NULL DEFAULT 0 COMMENT 'Порядок отображения',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_training_sections_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO training_sections (name, sort_order)
SELECT 'Общее', 0
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM training_sections WHERE name = 'Общее');

ALTER TABLE card_themes
    ADD COLUMN section_id INT NULL COMMENT 'FK training_sections.id' AFTER name,
    ADD INDEX idx_card_themes_section_id (section_id);

UPDATE card_themes
SET section_id = (SELECT id FROM training_sections WHERE name = 'Общее' LIMIT 1)
WHERE section_id IS NULL;

ALTER TABLE card_themes
    MODIFY section_id INT NOT NULL;
