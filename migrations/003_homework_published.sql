-- Видимость домашних заданий для студентов (1 = показано, 0 = скрыто)
ALTER TABLE homework
  ADD COLUMN published TINYINT(1) NOT NULL DEFAULT 1 AFTER deadline;

CREATE INDEX idx_homework_published ON homework (published);
