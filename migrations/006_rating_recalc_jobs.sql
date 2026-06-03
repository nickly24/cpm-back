-- =============================================================================
-- Журнал асинхронных пересчётов рейтинга
-- =============================================================================

CREATE TABLE IF NOT EXISTS rating_recalc_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    status ENUM('queued', 'running', 'completed', 'failed') NOT NULL DEFAULT 'queued',
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    created_by INT NULL COMMENT 'ID пользователя из JWT',
    created_by_name VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    completed_at DATETIME NULL,
    total_students INT NOT NULL DEFAULT 0,
    processed_count INT NOT NULL DEFAULT 0,
    successful INT NOT NULL DEFAULT 0,
    failed INT NOT NULL DEFAULT 0,
    skipped INT NOT NULL DEFAULT 0,
    message TEXT NULL,
    errors JSON NULL,
    INDEX idx_rating_recalc_status (status),
    INDEX idx_rating_recalc_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
