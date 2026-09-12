-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"outside_result_metadata","kind":"add_columns","table":"exam_sessions","columns":{"version":{"type":"bigint unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"requires":[]}
ALTER TABLE exam_sessions
 ADD COLUMN version BIGINT UNSIGNED NOT NULL DEFAULT 1,
 ADD COLUMN  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 ADD COLUMN  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6);

-- @step {"id":"outside_exam_result_import_sessions","kind":"create","table":"outside_exam_result_import_sessions","columns":{"id":{"type":"bigint","nullable":false},"exam_id":{"type":"int","nullable":false},"created_by_role":{"type":"varchar(20)","nullable":false},"created_by":{"type":"int","nullable":false},"source_filename":{"type":"varchar(255)","nullable":false},"preview_payload":{"type":"json","nullable":false},"preview_version":{"type":"bigint unsigned","nullable":false},"status":{"type":"varchar(16)","nullable":false},"commit_result":{"type":"json","nullable":true},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false},"expires_at":{"type":"datetime(6)","nullable":false},"committed_at":{"type":"datetime(6)","nullable":true}},"constraints":["fk_outside_import_exam","ck_outside_import_role","ck_outside_import_status"],"indexes":["ix_outside_import_owner"],"requires":[]}
CREATE TABLE outside_exam_result_import_sessions (
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
 KEY ix_outside_import_owner (exam_id,created_by_role,created_by,expires_at),
 CONSTRAINT fk_outside_import_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_outside_import_role CHECK (created_by_role IN ('admin','staff_admin')),
 CONSTRAINT ck_outside_import_status CHECK (status IN ('editable','committed'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
