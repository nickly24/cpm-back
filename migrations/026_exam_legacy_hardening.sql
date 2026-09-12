-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"outside_result_value_guards","kind":"constraints","table":"exam_sessions","constraints":["ck_exam_sessions_grade","ck_exam_sessions_nonnegative"],"requires":["SELECT COUNT(*) FROM exam_sessions WHERE points < 0 OR points > 5 OR points <> FLOOR(points) OR val < 0 OR val > 9999999999.99 OR val <> ROUND(val,2)"]}
ALTER TABLE exam_sessions ADD CONSTRAINT ck_exam_sessions_grade CHECK(points BETWEEN 0 AND 5 AND points = FLOOR(points)), ADD CONSTRAINT ck_exam_sessions_nonnegative CHECK(val BETWEEN 0 AND 9999999999.99 AND val = ROUND(val,2));

-- @step {"id":"outside_result_types","kind":"modify_columns","table":"exam_sessions","columns":{"val":{"type":"decimal(12,2)","nullable":false},"points":{"type":"tinyint unsigned","nullable":false},"examinator":{"type":"varchar(255)","nullable":false}},"before":{"val":{"type":"double","nullable":false},"points":{"type":"double","nullable":false},"examinator":{"type":"varchar(100)","nullable":false}},"requires":["SELECT COUNT(*) FROM exam_sessions WHERE points < 0 OR points > 5 OR points <> FLOOR(points) OR val < 0 OR val > 9999999999.99 OR val <> ROUND(val,2)","SELECT COUNT(*) FROM (SELECT exam_id,student_id FROM exam_sessions GROUP BY exam_id,student_id HAVING COUNT(*)>1) duplicates","SELECT COUNT(*) FROM exam_sessions es LEFT JOIN students s ON s.id=es.student_id LEFT JOIN exams e ON e.id=es.exam_id WHERE s.id IS NULL OR e.id IS NULL","SELECT COUNT(*) FROM (SELECT student_id FROM Allratings GROUP BY student_id HAVING COUNT(*)>1) duplicates","SELECT COUNT(*) FROM exams e LEFT JOIN directions d ON d.id=e.direction_id WHERE d.id IS NULL","SELECT COUNT(*) FROM rating_recalc_jobs WHERE status IN ('queued','running')"]}
ALTER TABLE exam_sessions MODIFY COLUMN val DECIMAL(12,2) NOT NULL, MODIFY COLUMN points TINYINT UNSIGNED NOT NULL, MODIFY COLUMN examinator VARCHAR(255) NOT NULL;

-- @step {"id":"outside_result_unique","kind":"indexes","table":"exam_sessions","indexes":["uq_exam_sessions_exam_student"],"requires":["SELECT COUNT(*) FROM (SELECT exam_id,student_id FROM exam_sessions GROUP BY exam_id,student_id HAVING COUNT(*)>1) duplicates"]}
ALTER TABLE exam_sessions ADD UNIQUE KEY uq_exam_sessions_exam_student(exam_id,student_id);

-- @step {"id":"outside_result_constraints","kind":"constraints","table":"exam_sessions","constraints":["fk_exam_sessions_exam","fk_exam_sessions_student"],"requires":["SELECT COUNT(*) FROM exam_sessions es LEFT JOIN students s ON s.id=es.student_id LEFT JOIN exams e ON e.id=es.exam_id WHERE s.id IS NULL OR e.id IS NULL","SELECT COUNT(*) FROM exam_sessions WHERE points < 0 OR points > 5 OR points <> FLOOR(points) OR val < 0 OR val > 9999999999.99 OR val <> ROUND(val,2)"]}
ALTER TABLE exam_sessions ADD CONSTRAINT fk_exam_sessions_exam FOREIGN KEY(exam_id) REFERENCES exams(id) ON DELETE CASCADE, ADD CONSTRAINT fk_exam_sessions_student FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE RESTRICT;

-- @step {"id":"rating_student_unique","kind":"indexes","table":"Allratings","indexes":["uq_allratings_student"],"requires":["SELECT COUNT(*) FROM (SELECT student_id FROM Allratings GROUP BY student_id HAVING COUNT(*)>1) duplicates"]}
ALTER TABLE Allratings ADD UNIQUE KEY uq_allratings_student(student_id);

-- @step {"id":"exam_direction_required","kind":"modify_columns","table":"exams","columns":{"direction_id":{"type":"int","nullable":false}},"before":{"direction_id":{"type":"int","nullable":true}},"requires":["SELECT COUNT(*) FROM exams e LEFT JOIN directions d ON d.id=e.direction_id WHERE d.id IS NULL"]}
ALTER TABLE exams MODIFY COLUMN direction_id INT NOT NULL;

-- @step {"id":"exam_domain_constraints","kind":"constraints","table":"exams","constraints":["fk_exams_direction","ck_exams_type"],"requires":["SELECT COUNT(*) FROM exams e LEFT JOIN directions d ON d.id=e.direction_id WHERE d.id IS NULL"]}
ALTER TABLE exams ADD CONSTRAINT fk_exams_direction FOREIGN KEY(direction_id) REFERENCES directions(id) ON DELETE RESTRICT, ADD CONSTRAINT ck_exams_type CHECK(exam_type IN ('outside_lms','classic'));
