-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_parts","kind":"create","table":"classic_exam_parts","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"code":{"type":"char(1)","nullable":false},"question_weight":{"type":"int unsigned","nullable":true},"question_count":{"type":"int unsigned","nullable":true},"sort_order":{"type":"int unsigned","nullable":false},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_part_exam","ck_classic_part_code","ck_classic_part_weight","ck_classic_part_count"],"indexes":["uq_classic_part_code","uq_classic_part_order"],"requires":[]}
CREATE TABLE classic_exam_parts (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 code CHAR(1) NOT NULL,
 question_weight INT UNSIGNED NULL,
 question_count INT UNSIGNED NULL,
 sort_order INT UNSIGNED NOT NULL,
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_part_code (exam_id,code),
 UNIQUE KEY uq_classic_part_order (exam_id,sort_order),
 CONSTRAINT fk_classic_part_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_part_code CHECK (ASCII(code) BETWEEN 65 AND 90),
 CONSTRAINT ck_classic_part_weight CHECK (question_weight IS NULL OR question_weight BETWEEN 1 AND 1000),
 CONSTRAINT ck_classic_part_count CHECK (question_count IS NULL OR question_count BETWEEN 1 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_questions","kind":"create","table":"classic_exam_questions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"part_id":{"type":"bigint","nullable":false},"question_text":{"type":"text","nullable":false},"answer_text":{"type":"text","nullable":false},"content_hash":{"type":"char(64)","nullable":false},"sort_order":{"type":"int unsigned","nullable":false},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_question_exam","fk_classic_question_part"],"indexes":["uq_classic_question_content","uq_classic_question_order","ix_classic_question_exam_part"],"requires":[]}
CREATE TABLE classic_exam_questions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 part_id BIGINT NOT NULL,
 question_text TEXT NOT NULL,
 answer_text TEXT NOT NULL,
 content_hash CHAR(64) NOT NULL,
 sort_order INT UNSIGNED NOT NULL,
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_question_content (part_id,content_hash),
 UNIQUE KEY uq_classic_question_order (part_id,sort_order),
 KEY ix_classic_question_exam_part (exam_id,part_id),
 CONSTRAINT fk_classic_question_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_question_part FOREIGN KEY (part_id) REFERENCES classic_exam_parts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"settings_part_reference","kind":"constraints","table":"classic_exam_settings","constraints":["fk_classic_settings_part"],"requires":[]}
ALTER TABLE classic_exam_settings ADD CONSTRAINT fk_classic_settings_part FOREIGN KEY(tie_breaker_part_id) REFERENCES classic_exam_parts(id) ON DELETE SET NULL;

-- @step {"id":"classic_exam_question_import_sessions","kind":"create","table":"classic_exam_question_import_sessions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"created_by_role":{"type":"varchar(20)","nullable":false},"created_by":{"type":"int","nullable":false},"source_filename":{"type":"varchar(255)","nullable":false},"preview_payload":{"type":"json","nullable":false},"preview_version":{"type":"bigint unsigned","nullable":false},"status":{"type":"varchar(16)","nullable":false},"commit_result":{"type":"json","nullable":true},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false},"expires_at":{"type":"datetime(6)","nullable":false},"committed_at":{"type":"datetime(6)","nullable":true}},"constraints":["fk_question_import_exam","ck_question_import_role","ck_question_import_status"],"indexes":["ix_question_import_owner"],"requires":[]}
CREATE TABLE classic_exam_question_import_sessions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 created_by_role VARCHAR(20) NOT NULL,
 created_by INT NOT NULL,
 source_filename VARCHAR(255) NOT NULL,
 preview_payload JSON NOT NULL,
 preview_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 status VARCHAR(16) NOT NULL DEFAULT 'editable',
 commit_result JSON NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 expires_at DATETIME(6) NOT NULL,
 committed_at DATETIME(6) NULL,
 KEY ix_question_import_owner (exam_id,created_by_role,created_by,expires_at),
 CONSTRAINT fk_question_import_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_question_import_role CHECK (created_by_role IN ('admin','staff_admin')),
 CONSTRAINT ck_question_import_status CHECK (status IN ('editable','committed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
