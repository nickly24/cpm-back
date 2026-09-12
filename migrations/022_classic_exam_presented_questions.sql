-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_attempt_part_progress","kind":"create","table":"classic_exam_attempt_part_progress","columns":{"attempt_id":{"type":"bigint","nullable":false},"source_part_id":{"type":"bigint","nullable":false},"part_code":{"type":"char(1)","nullable":false},"question_weight":{"type":"int unsigned","nullable":false},"required_count":{"type":"int unsigned","nullable":false},"consensus_count":{"type":"int unsigned","nullable":false},"cycle_no":{"type":"int unsigned","nullable":false}},"constraints":["fk_classic_progress_attempt","ck_classic_progress_count"],"indexes":[],"requires":[]}
CREATE TABLE classic_exam_attempt_part_progress (
 attempt_id BIGINT NOT NULL,
 source_part_id BIGINT NOT NULL,
 part_code CHAR(1) NOT NULL,
 question_weight INT UNSIGNED NOT NULL,
 required_count INT UNSIGNED NOT NULL,
 consensus_count INT UNSIGNED NOT NULL DEFAULT 0,
 cycle_no INT UNSIGNED NOT NULL DEFAULT 1,
 PRIMARY KEY(attempt_id,source_part_id),
 CONSTRAINT fk_classic_progress_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_progress_count CHECK (consensus_count <= required_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_presented_questions","kind":"create","table":"classic_exam_presented_questions","columns":{"id":{"type":"bigint","nullable":false},"attempt_id":{"type":"bigint","nullable":false},"definition_question_id":{"type":"bigint","nullable":false},"sequence_no":{"type":"bigint unsigned","nullable":false},"purpose":{"type":"varchar(24)","nullable":false},"cycle_no":{"type":"int unsigned","nullable":false},"status":{"type":"varchar(24)","nullable":false},"replaces_presented_question_id":{"type":"bigint","nullable":true},"replaced_by_examinator_id":{"type":"int","nullable":true},"replaced_at":{"type":"datetime(6)","nullable":true},"created_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_presented_attempt","fk_classic_presented_definition","fk_classic_presented_replacer","ck_classic_presented_purpose","ck_classic_presented_status"],"indexes":["uq_classic_presented_sequence","ix_classic_presented_cycle","ix_classic_presented_status"],"requires":[]}
CREATE TABLE classic_exam_presented_questions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 attempt_id BIGINT NOT NULL,
 definition_question_id BIGINT NOT NULL,
 sequence_no BIGINT UNSIGNED NOT NULL,
 purpose VARCHAR(24) NOT NULL,
 cycle_no INT UNSIGNED NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'open',
 replaces_presented_question_id BIGINT NULL,
 replaced_by_examinator_id INT NULL,
 replaced_at DATETIME(6) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_presented_sequence (attempt_id,sequence_no),
 KEY ix_classic_presented_cycle (attempt_id,definition_question_id,cycle_no),
 KEY ix_classic_presented_status (attempt_id,status,purpose),
 CONSTRAINT fk_classic_presented_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_presented_definition FOREIGN KEY (definition_question_id) REFERENCES classic_exam_definition_questions(id) ON DELETE RESTRICT,
 CONSTRAINT fk_classic_presented_replacer FOREIGN KEY (replaced_by_examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_presented_purpose CHECK (purpose IN ('regular','tie_breaker')),
 CONSTRAINT ck_classic_presented_status CHECK (status IN ('open','replaced','consensus'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_attempt_question_usage","kind":"create","table":"classic_exam_attempt_question_usage","columns":{"attempt_id":{"type":"bigint","nullable":false},"definition_question_id":{"type":"bigint","nullable":false},"last_cycle_no":{"type":"int unsigned","nullable":false},"ever_presented":{"type":"tinyint(1)","nullable":false},"permanently_excluded":{"type":"tinyint(1)","nullable":false}},"constraints":["fk_classic_usage_attempt","fk_classic_usage_definition"],"indexes":[],"requires":[]}
CREATE TABLE classic_exam_attempt_question_usage (
 attempt_id BIGINT NOT NULL,
 definition_question_id BIGINT NOT NULL,
 last_cycle_no INT UNSIGNED NOT NULL,
 ever_presented BOOLEAN NOT NULL DEFAULT FALSE,
 permanently_excluded BOOLEAN NOT NULL DEFAULT FALSE,
 PRIMARY KEY(attempt_id,definition_question_id),
 CONSTRAINT fk_classic_usage_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_usage_definition FOREIGN KEY (definition_question_id) REFERENCES classic_exam_definition_questions(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"attempt_current_pointers","kind":"add_columns","table":"classic_exam_attempts","columns":{"current_presented_question_id":{"type":"bigint","nullable":true},"last_definition_question_id":{"type":"bigint","nullable":true}},"requires":[]}
ALTER TABLE classic_exam_attempts
 ADD COLUMN current_presented_question_id BIGINT NULL,
 ADD COLUMN  last_definition_question_id BIGINT NULL;
