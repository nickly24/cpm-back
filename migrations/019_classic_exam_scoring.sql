-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_grade_thresholds","kind":"create","table":"classic_exam_grade_thresholds","columns":{"exam_id":{"type":"int","nullable":false},"grade":{"type":"tinyint unsigned","nullable":false},"min_score":{"type":"int unsigned","nullable":false},"created_at":{"type":"datetime(6)","nullable":false},"updated_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_threshold_exam","ck_classic_threshold_grade","ck_classic_threshold_zero"],"indexes":[],"requires":[]}
CREATE TABLE classic_exam_grade_thresholds (
 exam_id INT NOT NULL,
 grade TINYINT UNSIGNED NOT NULL,
 min_score INT UNSIGNED NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 PRIMARY KEY(exam_id,grade),
 CONSTRAINT fk_classic_threshold_exam FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_threshold_grade CHECK (grade BETWEEN 0 AND 5),
 CONSTRAINT ck_classic_threshold_zero CHECK (grade <> 0 OR min_score = 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
