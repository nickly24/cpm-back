-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"rating_source_state","kind":"create","table":"rating_source_state","columns":{"id":{"type":"tinyint","nullable":false},"source_revision":{"type":"bigint unsigned","nullable":false},"calculated_revision":{"type":"bigint unsigned","nullable":true},"date_from":{"type":"date","nullable":true},"date_to":{"type":"date","nullable":true},"calculated_at":{"type":"datetime(6)","nullable":true},"as_of":{"type":"datetime(6)","nullable":true},"next_exam_start_at":{"type":"datetime(6)","nullable":true},"active_job_id":{"type":"int","nullable":true},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["ck_rating_source_singleton"],"indexes":[],"requires":[]}
CREATE TABLE rating_source_state (
 id TINYINT NOT NULL PRIMARY KEY,
 source_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
 calculated_revision BIGINT UNSIGNED NULL,
 date_from DATE NULL,
 date_to DATE NULL,
 calculated_at DATETIME(6) NULL,
 as_of DATETIME(6) NULL,
 next_exam_start_at DATETIME(6) NULL,
 active_job_id INT NULL,
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 CONSTRAINT ck_rating_source_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"rating_source_seed","kind":"seed","table":"rating_source_state","post_sql":"SELECT COUNT(*) = 1 FROM rating_source_state WHERE id=1","requires":[]}
INSERT INTO rating_source_state(id) VALUES(1);

-- @step {"id":"published_rating_details","kind":"add_columns","table":"Allratings","columns":{"details_json":{"type":"json","nullable":true},"calculation_job_id":{"type":"int","nullable":true}},"requires":[]}
ALTER TABLE Allratings
 ADD COLUMN details_json JSON NULL,
 ADD COLUMN  calculation_job_id INT NULL;

-- @step {"id":"rating_worker_lease","kind":"add_columns","table":"rating_recalc_jobs","columns":{"exam_source_revision":{"type":"bigint unsigned","nullable":true},"as_of":{"type":"datetime(6)","nullable":true},"worker_token":{"type":"char(36)","nullable":true},"heartbeat_at":{"type":"datetime(6)","nullable":true}},"requires":[]}
ALTER TABLE rating_recalc_jobs
 ADD COLUMN exam_source_revision BIGINT UNSIGNED NULL,
 ADD COLUMN  as_of DATETIME(6) NULL,
 ADD COLUMN  worker_token CHAR(36) NULL,
 ADD COLUMN  heartbeat_at DATETIME(6) NULL;

-- @step {"id":"rating_recalc_staging","kind":"create","table":"rating_recalc_staging","columns":{"job_id":{"type":"int","nullable":false},"student_id":{"type":"int","nullable":false},"exams":{"type":"float","nullable":false},"homework":{"type":"float","nullable":false},"tests":{"type":"float","nullable":false},"final":{"type":"float","nullable":false},"details_json":{"type":"json","nullable":false}},"constraints":["fk_rating_staging_job"],"indexes":[],"requires":[]}
CREATE TABLE rating_recalc_staging (
 job_id INT NOT NULL,
 student_id INT NOT NULL,
 exams FLOAT NOT NULL,
 homework FLOAT NOT NULL,
 tests FLOAT NOT NULL,
 final FLOAT NOT NULL,
 details_json JSON NOT NULL,
 PRIMARY KEY(job_id,student_id),
 CONSTRAINT fk_rating_staging_job FOREIGN KEY (job_id) REFERENCES rating_recalc_jobs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
