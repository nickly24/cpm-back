-- =============================================================================
-- Массовый импорт пользователей: сессии preview, журнал jobs, результаты
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_import_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    import_type VARCHAR(64) NOT NULL DEFAULT 'users',
    source_filename VARCHAR(255) NULL,
    preview_payload JSON NOT NULL,
    created_by INT NULL,
    created_by_name VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    INDEX idx_user_import_sessions_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_import_jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    import_type VARCHAR(64) NOT NULL DEFAULT 'users',
    status ENUM('queued', 'running', 'rolling_back', 'completed', 'failed') NOT NULL DEFAULT 'queued',
    created_by INT NULL,
    created_by_name VARCHAR(255) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    completed_at DATETIME NULL,
    total_rows INT NOT NULL DEFAULT 0,
    processed_count INT NOT NULL DEFAULT 0,
    successful INT NOT NULL DEFAULT 0,
    skipped INT NOT NULL DEFAULT 0,
    failed INT NOT NULL DEFAULT 0,
    message TEXT NULL,
    errors JSON NULL,
    entities_created JSON NULL,
    summary JSON NULL,
    INDEX idx_user_import_jobs_status (status),
    INDEX idx_user_import_jobs_created_at (created_at),
    INDEX idx_user_import_jobs_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_import_job_results (
    job_id INT NOT NULL PRIMARY KEY,
    rows_data JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_user_import_job_results_job
        FOREIGN KEY (job_id) REFERENCES user_import_jobs(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
