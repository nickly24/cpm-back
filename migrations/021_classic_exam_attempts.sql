-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_definition_versions","kind":"create","table":"classic_exam_definition_versions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"config_version":{"type":"bigint unsigned","nullable":false},"config_json":{"type":"json","nullable":false},"created_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_definition_exam"],"indexes":["uq_classic_definition_version"],"requires":[]}
CREATE TABLE classic_exam_definition_versions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 config_version BIGINT UNSIGNED NOT NULL,
 config_json JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_definition_version (exam_id,config_version),
 CONSTRAINT fk_classic_definition_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_definition_questions","kind":"create","table":"classic_exam_definition_questions","columns":{"id":{"type":"bigint","nullable":false},"definition_id":{"type":"bigint","nullable":false},"source_question_id":{"type":"bigint","nullable":false},"source_part_id":{"type":"bigint","nullable":false},"part_code":{"type":"char(1)","nullable":false},"question_text":{"type":"text","nullable":false},"answer_text":{"type":"text","nullable":false}},"constraints":["fk_classic_dq_definition"],"indexes":["uq_classic_dq_source","ix_classic_dq_part"],"requires":[]}
CREATE TABLE classic_exam_definition_questions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 definition_id BIGINT NOT NULL,
 source_question_id BIGINT NOT NULL,
 source_part_id BIGINT NOT NULL,
 part_code CHAR(1) NOT NULL,
 question_text TEXT NOT NULL,
 answer_text TEXT NOT NULL,
 UNIQUE KEY uq_classic_dq_source (definition_id,source_question_id),
 KEY ix_classic_dq_part (definition_id,source_part_id,id),
 CONSTRAINT fk_classic_dq_definition FOREIGN KEY (definition_id) REFERENCES classic_exam_definition_versions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_attempts","kind":"create","table":"classic_exam_attempts","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"student_id":{"type":"int","nullable":false},"assignment_id":{"type":"bigint","nullable":false},"definition_id":{"type":"bigint","nullable":true},"attempt_no":{"type":"tinyint unsigned","nullable":false},"status":{"type":"varchar(24)","nullable":false},"phase":{"type":"varchar(32)","nullable":false},"state_version":{"type":"bigint unsigned","nullable":false},"history_generation":{"type":"bigint unsigned","nullable":false},"student_name_snapshot":{"type":"varchar(255)","nullable":false},"replacement_limit_snapshot":{"type":"int unsigned","nullable":true},"replacement_used":{"type":"int unsigned","nullable":false},"started_at":{"type":"datetime(6)","nullable":true},"completed_at":{"type":"datetime(6)","nullable":true},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_attempt_exam","fk_classic_attempt_student","fk_classic_attempt_assignment","fk_classic_attempt_definition","ck_classic_attempt_no","ck_classic_attempt_status","ck_classic_attempt_phase","ck_classic_attempt_replacements"],"indexes":["uq_classic_attempt_student_no","uq_classic_attempt_assignment","ix_classic_attempt_exam_status","ix_classic_attempt_student_exam"],"requires":[]}
CREATE TABLE classic_exam_attempts (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 student_id INT NOT NULL,
 assignment_id BIGINT NOT NULL,
 definition_id BIGINT NULL,
 attempt_no TINYINT UNSIGNED NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'pending_ready',
 phase VARCHAR(32) NOT NULL DEFAULT 'preparation',
 state_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 history_generation BIGINT UNSIGNED NOT NULL,
 student_name_snapshot VARCHAR(255) NOT NULL,
 replacement_limit_snapshot INT UNSIGNED NULL,
 replacement_used INT UNSIGNED NOT NULL DEFAULT 0,
 started_at DATETIME(6) NULL,
 completed_at DATETIME(6) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_attempt_student_no (exam_id,student_id,attempt_no),
 UNIQUE KEY uq_classic_attempt_assignment (assignment_id),
 KEY ix_classic_attempt_exam_status (exam_id,status,id),
 KEY ix_classic_attempt_student_exam (student_id,exam_id,id),
 CONSTRAINT fk_classic_attempt_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_attempt_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT,
 CONSTRAINT fk_classic_attempt_assignment FOREIGN KEY (assignment_id) REFERENCES classic_exam_assignments(id) ON DELETE RESTRICT,
 CONSTRAINT fk_classic_attempt_definition FOREIGN KEY (definition_id) REFERENCES classic_exam_definition_versions(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_attempt_no CHECK (attempt_no IN (1,2)),
 CONSTRAINT ck_classic_attempt_status CHECK (status IN ('pending_ready','in_progress','completed')),
 CONSTRAINT ck_classic_attempt_phase CHECK (phase IN ('preparation','regular_questions','tie_breaker','completed')),
 CONSTRAINT ck_classic_attempt_replacements CHECK (replacement_limit_snapshot IS NULL OR replacement_used <= replacement_limit_snapshot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_attempt_members","kind":"create","table":"classic_exam_attempt_members","columns":{"attempt_id":{"type":"bigint","nullable":false},"examinator_id":{"type":"int","nullable":false},"full_name_snapshot":{"type":"varchar(255)","nullable":false},"position":{"type":"tinyint unsigned","nullable":false},"ready_at":{"type":"datetime(6)","nullable":true}},"constraints":["fk_classic_atm_attempt","fk_classic_atm_examinator","ck_classic_atm_position"],"indexes":["uq_classic_atm_position"],"requires":[]}
CREATE TABLE classic_exam_attempt_members (
 attempt_id BIGINT NOT NULL,
 examinator_id INT NOT NULL,
 full_name_snapshot VARCHAR(255) NOT NULL,
 position TINYINT UNSIGNED NOT NULL,
 ready_at DATETIME(6) NULL,
 PRIMARY KEY(attempt_id,examinator_id),
 UNIQUE KEY uq_classic_atm_position (attempt_id,position),
 CONSTRAINT fk_classic_atm_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_atm_examinator FOREIGN KEY (examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_atm_position CHECK (position BETWEEN 1 AND 6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_attempt_commands","kind":"create","table":"classic_exam_attempt_commands","columns":{"id":{"type":"bigint","nullable":false},"attempt_id":{"type":"bigint","nullable":false},"actor_examinator_id":{"type":"int","nullable":false},"idempotency_key":{"type":"char(36)","nullable":false},"command_type":{"type":"varchar(32)","nullable":false},"request_hash":{"type":"char(64)","nullable":false},"receipt":{"type":"json","nullable":false},"created_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_command_attempt","fk_classic_command_examinator"],"indexes":["uq_classic_command_actor_key"],"requires":[]}
CREATE TABLE classic_exam_attempt_commands (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 attempt_id BIGINT NOT NULL,
 actor_examinator_id INT NOT NULL,
 idempotency_key CHAR(36) NOT NULL,
 command_type VARCHAR(32) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 receipt JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_command_actor_key (attempt_id,actor_examinator_id,idempotency_key),
 CONSTRAINT fk_classic_command_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_command_examinator FOREIGN KEY (actor_examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
