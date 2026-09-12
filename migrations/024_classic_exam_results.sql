-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"attempt_result_columns","kind":"add_columns","table":"classic_exam_attempts","columns":{"raw_total":{"type":"decimal(12,1)","nullable":true},"rounded_total":{"type":"int unsigned","nullable":true},"max_score":{"type":"int unsigned","nullable":true},"calculated_grade":{"type":"tinyint unsigned","nullable":true},"resolution_type":{"type":"varchar(32)","nullable":true},"result_version":{"type":"bigint unsigned","nullable":false},"published_at":{"type":"datetime(6)","nullable":true}},"requires":[]}
ALTER TABLE classic_exam_attempts
 ADD COLUMN raw_total DECIMAL(12,1) NULL,
 ADD COLUMN  rounded_total INT UNSIGNED NULL,
 ADD COLUMN  max_score INT UNSIGNED NULL,
 ADD COLUMN  calculated_grade TINYINT UNSIGNED NULL,
 ADD COLUMN  resolution_type VARCHAR(32) NULL,
 ADD COLUMN  result_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 ADD COLUMN  published_at DATETIME(6) NULL;

-- @step {"id":"classic_exam_appeals","kind":"create","table":"classic_exam_appeals","columns":{"id":{"type":"bigint","nullable":false},"attempt_id":{"type":"bigint","nullable":false},"previous_grade":{"type":"tinyint unsigned","nullable":false},"new_grade":{"type":"tinyint unsigned","nullable":false},"changed_by_role":{"type":"varchar(20)","nullable":false},"changed_by_admin_id":{"type":"int","nullable":false},"admin_name_snapshot":{"type":"varchar(255)","nullable":false},"created_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_appeal_attempt","ck_classic_appeal_previous","ck_classic_appeal_new","ck_classic_appeal_actor"],"indexes":["ix_classic_appeal_attempt"],"requires":[]}
CREATE TABLE classic_exam_appeals (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 attempt_id BIGINT NOT NULL,
 previous_grade TINYINT UNSIGNED NOT NULL,
 new_grade TINYINT UNSIGNED NOT NULL,
 changed_by_role VARCHAR(20) NOT NULL,
 changed_by_admin_id INT NOT NULL,
 admin_name_snapshot VARCHAR(255) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 KEY ix_classic_appeal_attempt (attempt_id,id),
 CONSTRAINT fk_classic_appeal_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_appeal_previous CHECK (previous_grade BETWEEN 0 AND 5),
 CONSTRAINT ck_classic_appeal_new CHECK (new_grade BETWEEN 0 AND 5),
 CONSTRAINT ck_classic_appeal_actor CHECK (changed_by_role IN ('admin','staff_admin'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"result_grade_check","kind":"constraints","table":"classic_exam_attempts","constraints":["ck_classic_attempt_grade"],"requires":[]}
ALTER TABLE classic_exam_attempts ADD CONSTRAINT ck_classic_attempt_grade CHECK(calculated_grade IS NULL OR calculated_grade BETWEEN 0 AND 5);
