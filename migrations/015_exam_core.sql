-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"exam_columns","kind":"add_columns","table":"exams","columns":{"exam_type":{"type":"varchar(20)","nullable":false},"direction_id":{"type":"int","nullable":true},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"requires":[]}
ALTER TABLE exams
 ADD COLUMN exam_type VARCHAR(20) NOT NULL DEFAULT 'outside_lms',
 ADD COLUMN  direction_id INT NULL,
 ADD COLUMN  version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 ADD COLUMN  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 ADD COLUMN  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6);

-- @step {"id":"exam_nullable_fields","kind":"modify_columns","table":"exams","columns":{"name":{"type":"varchar(60)","nullable":true},"date":{"type":"date","nullable":true}},"before":{"name":{"type":"varchar(60)","nullable":false},"date":{"type":"date","nullable":false}},"requires":[]}
ALTER TABLE exams MODIFY COLUMN name VARCHAR(60) NULL, MODIFY COLUMN date DATE NULL;

-- @step {"id":"exam_indexes","kind":"indexes","table":"exams","indexes":["idx_exams_type_date","idx_exams_direction"],"requires":[]}
ALTER TABLE exams ADD KEY idx_exams_type_date(exam_type,date), ADD KEY idx_exams_direction(direction_id);

-- @step {"id":"exam_admin_commands","kind":"create","table":"exam_admin_commands","columns":{"id":{"type":"bigint","nullable":false},"actor_role":{"type":"varchar(20)","nullable":false},"actor_id":{"type":"int","nullable":false},"idempotency_key":{"type":"char(36)","nullable":false},"command_type":{"type":"varchar(64)","nullable":false},"target_exam_id":{"type":"int","nullable":true},"target_student_id":{"type":"int","nullable":true},"target_attempt_id":{"type":"bigint","nullable":true},"request_hash":{"type":"char(64)","nullable":false},"receipt":{"type":"json","nullable":true},"tombstone":{"type":"tinyint(1)","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"expires_at":{"type":"datetime(6)","nullable":false}},"constraints":["ck_eac_actor_role"],"indexes":["uq_eac_actor_key","ix_eac_exam_expiry"],"requires":[]}
CREATE TABLE exam_admin_commands (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 actor_role VARCHAR(20) NOT NULL,
 actor_id INT NOT NULL,
 idempotency_key CHAR(36) NOT NULL,
 command_type VARCHAR(64) NOT NULL,
 target_exam_id INT NULL,
 target_student_id INT NULL,
 target_attempt_id BIGINT NULL,
 request_hash CHAR(64) NOT NULL,
 receipt JSON NULL,
 tombstone BOOLEAN NOT NULL DEFAULT FALSE,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 expires_at DATETIME(6) NOT NULL,
 UNIQUE KEY uq_eac_actor_key (actor_role,actor_id,idempotency_key),
 KEY ix_eac_exam_expiry (target_exam_id,expires_at),
 CONSTRAINT ck_eac_actor_role CHECK (actor_role IN ('admin','staff_admin'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
