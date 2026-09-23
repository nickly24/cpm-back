-- Additive exam flashcards migration. Run through scripts/exam_training_migration.py.
-- Existing progress, settings, exam questions and results are preserved.
-- Every statement is independently resumable after its schema postcondition.
ALTER TABLE exams ADD COLUMN training_enabled TINYINT(1) NOT NULL DEFAULT 0;
ALTER TABLE student_card_progress MODIFY COLUMN section_kind ENUM('manual','test','exam') NOT NULL;
ALTER TABLE student_card_progress MODIFY COLUMN section_ref_id VARCHAR(64) NOT NULL;
ALTER TABLE student_section_study_settings MODIFY COLUMN section_kind ENUM('manual','test','exam') NOT NULL;
ALTER TABLE student_section_study_settings MODIFY COLUMN section_ref_id VARCHAR(64) NOT NULL;
ALTER TABLE student_section_study_settings ADD COLUMN last_part_id BIGINT NULL;
