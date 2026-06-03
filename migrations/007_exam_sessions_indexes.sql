-- Индексы для пагинации и фильтрации exam_sessions
CREATE INDEX idx_exam_sessions_exam_id ON exam_sessions (exam_id);
CREATE INDEX idx_exam_sessions_student_id ON exam_sessions (student_id);
CREATE INDEX idx_exams_date ON exams (date);
