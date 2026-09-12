-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_commissions","kind":"create","table":"classic_exam_commissions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"name":{"type":"varchar(120)","nullable":false},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_commission_exam"],"indexes":["uq_classic_commission_name"],"requires":[]}
CREATE TABLE classic_exam_commissions (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 name VARCHAR(120) NOT NULL,
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_commission_name (exam_id,name),
 CONSTRAINT fk_classic_commission_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_commission_members","kind":"create","table":"classic_exam_commission_members","columns":{"commission_id":{"type":"bigint","nullable":false},"examinator_id":{"type":"int","nullable":false},"position":{"type":"smallint unsigned","nullable":false}},"constraints":["fk_classic_cm_commission","fk_classic_cm_examinator","ck_classic_cm_position"],"indexes":["uq_classic_cm_position"],"requires":[]}
CREATE TABLE classic_exam_commission_members (
 commission_id BIGINT NOT NULL,
 examinator_id INT NOT NULL,
 position SMALLINT UNSIGNED NOT NULL,
 PRIMARY KEY(commission_id,examinator_id),
 UNIQUE KEY uq_classic_cm_position (commission_id,position),
 CONSTRAINT fk_classic_cm_commission FOREIGN KEY (commission_id) REFERENCES classic_exam_commissions(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_cm_examinator FOREIGN KEY (examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_cm_position CHECK (position BETWEEN 1 AND 6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_assignments","kind":"create","table":"classic_exam_assignments","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"student_id":{"type":"int","nullable":false},"attempt_no":{"type":"tinyint unsigned","nullable":false},"source_commission_id":{"type":"bigint","nullable":true},"created_by_role":{"type":"varchar(20)","nullable":false},"created_by":{"type":"int","nullable":false},"commission_name_snapshot":{"type":"varchar(120)","nullable":false},"history_generation":{"type":"bigint unsigned","nullable":false},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_assignment_exam","fk_classic_assignment_student","fk_classic_assignment_commission","ck_classic_assignment_attempt","ck_classic_assignment_actor"],"indexes":["uq_classic_assignment_attempt"],"requires":[]}
CREATE TABLE classic_exam_assignments (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 exam_id INT NOT NULL,
 student_id INT NOT NULL,
 attempt_no TINYINT UNSIGNED NOT NULL DEFAULT 1,
 source_commission_id BIGINT NULL,
 created_by_role VARCHAR(20) NOT NULL,
 created_by INT NOT NULL,
 commission_name_snapshot VARCHAR(120) NOT NULL,
 history_generation BIGINT UNSIGNED NOT NULL DEFAULT 1,
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_assignment_attempt (exam_id,student_id,attempt_no),
 CONSTRAINT fk_classic_assignment_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_assignment_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT,
 CONSTRAINT fk_classic_assignment_commission FOREIGN KEY (source_commission_id) REFERENCES classic_exam_commissions(id) ON DELETE SET NULL,
 CONSTRAINT ck_classic_assignment_attempt CHECK (attempt_no IN (1,2)),
 CONSTRAINT ck_classic_assignment_actor CHECK (created_by_role IN ('admin','staff_admin'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_assignment_members","kind":"create","table":"classic_exam_assignment_members","columns":{"assignment_id":{"type":"bigint","nullable":false},"examinator_id":{"type":"int","nullable":false},"position":{"type":"smallint unsigned","nullable":false},"full_name_snapshot":{"type":"varchar(255)","nullable":false}},"constraints":["fk_classic_am_assignment","fk_classic_am_examinator","ck_classic_am_position"],"indexes":["uq_classic_am_position"],"requires":[]}
CREATE TABLE classic_exam_assignment_members (
 assignment_id BIGINT NOT NULL,
 examinator_id INT NOT NULL,
 position SMALLINT UNSIGNED NOT NULL,
 full_name_snapshot VARCHAR(255) NOT NULL,
 PRIMARY KEY(assignment_id,examinator_id),
 UNIQUE KEY uq_classic_am_position (assignment_id,position),
 CONSTRAINT fk_classic_am_assignment FOREIGN KEY (assignment_id) REFERENCES classic_exam_assignments(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_am_examinator FOREIGN KEY (examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_am_position CHECK (position BETWEEN 1 AND 6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_student_privileges","kind":"create","table":"classic_exam_student_privileges","columns":{"exam_id":{"type":"int","nullable":false},"student_id":{"type":"int","nullable":false},"replacement_limit":{"type":"int unsigned","nullable":false},"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_privilege_exam","fk_classic_privilege_student","ck_classic_privilege_limit"],"indexes":[],"requires":[]}
CREATE TABLE classic_exam_student_privileges (
 exam_id INT NOT NULL,
 student_id INT NOT NULL,
 replacement_limit INT UNSIGNED NOT NULL DEFAULT 0,
 version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 PRIMARY KEY(exam_id,student_id),
 CONSTRAINT fk_classic_privilege_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_privilege_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_privilege_limit CHECK (replacement_limit BETWEEN 0 AND 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_assignment_import_sessions","kind":"create","table":"classic_exam_assignment_import_sessions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"created_by_role":{"type":"varchar(20)","nullable":false},"created_by":{"type":"int","nullable":false},"source_filename":{"type":"varchar(255)","nullable":false},"preview_payload":{"type":"json","nullable":false},"preview_version":{"type":"bigint unsigned","nullable":false},"status":{"type":"varchar(16)","nullable":false},"commit_result":{"type":"json","nullable":true},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false},"expires_at":{"type":"datetime(6)","nullable":false},"committed_at":{"type":"datetime(6)","nullable":true}},"constraints":["fk_assignment_import_exam","ck_assignment_import_role","ck_assignment_import_status"],"indexes":["ix_assignment_import_owner"],"requires":[]}
CREATE TABLE classic_exam_assignment_import_sessions (
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
 KEY ix_assignment_import_owner (exam_id,created_by_role,created_by,expires_at),
 CONSTRAINT fk_assignment_import_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_assignment_import_role CHECK (created_by_role IN ('admin','staff_admin')),
 CONSTRAINT ck_assignment_import_status CHECK (status IN ('editable','committed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
