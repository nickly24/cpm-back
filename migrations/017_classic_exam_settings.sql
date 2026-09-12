-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_settings","kind":"create","table":"classic_exam_settings","columns":{"exam_id":{"type":"int","nullable":false},"start_at":{"type":"datetime(6)","nullable":true},"end_at":{"type":"datetime(6)","nullable":true},"fractional_mode":{"type":"varchar(32)","nullable":true},"tie_breaker_source_mode":{"type":"varchar(32)","nullable":true},"tie_breaker_part_id":{"type":"bigint","nullable":true},"tie_breaker_half_mode":{"type":"varchar(32)","nullable":true},"config_version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_settings_exam","ck_classic_settings_window","ck_classic_settings_fraction","ck_classic_settings_source","ck_classic_settings_half"],"indexes":["ix_classic_settings_start"],"requires":[]}
CREATE TABLE classic_exam_settings (
 exam_id INT NOT NULL PRIMARY KEY,
 start_at DATETIME(6) NULL,
 end_at DATETIME(6) NULL,
 fractional_mode VARCHAR(32) NULL,
 tie_breaker_source_mode VARCHAR(32) NULL,
 tie_breaker_part_id BIGINT NULL,
 tie_breaker_half_mode VARCHAR(32) NULL,
 config_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 KEY ix_classic_settings_start (start_at,exam_id),
 CONSTRAINT fk_classic_settings_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_settings_window CHECK (end_at IS NULL OR start_at IS NULL OR end_at > start_at),
 CONSTRAINT ck_classic_settings_fraction CHECK (fractional_mode IS NULL OR fractional_mode IN ('round_up','round_down','extra_question')),
 CONSTRAINT ck_classic_settings_source CHECK (tie_breaker_source_mode IS NULL OR tie_breaker_source_mode IN ('specific_part','any_part')),
 CONSTRAINT ck_classic_settings_half CHECK (tie_breaker_half_mode IS NULL OR tie_breaker_half_mode IN ('repeat','round_up','round_down'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
