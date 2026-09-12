-- Classic exam domain migration. Run only through scripts/exams_migrations.py.
-- MySQL 8.0.22; legacy entity IDs are signed INT. Never run historical014 here.

-- @step {"id":"classic_exam_vote_rounds","kind":"create","table":"classic_exam_vote_rounds","columns":{"id":{"type":"bigint","nullable":false},"attempt_id":{"type":"bigint","nullable":false},"presented_question_id":{"type":"bigint","nullable":false},"round_no":{"type":"int unsigned","nullable":false},"status":{"type":"varchar(24)","nullable":false},"consensus_value":{"type":"decimal(2,1)","nullable":true},"opened_at":{"type":"datetime(6)","nullable":false},"completed_at":{"type":"datetime(6)","nullable":true}},"constraints":["fk_classic_round_attempt","fk_classic_round_presented","ck_classic_round_status","ck_classic_round_consensus"],"indexes":["uq_classic_round_no","ix_classic_round_attempt_status"],"requires":[]}
CREATE TABLE classic_exam_vote_rounds (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 attempt_id BIGINT NOT NULL,
 presented_question_id BIGINT NOT NULL,
 round_no INT UNSIGNED NOT NULL,
 status VARCHAR(24) NOT NULL DEFAULT 'open',
 consensus_value DECIMAL(2,1) NULL,
 opened_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 completed_at DATETIME(6) NULL,
 UNIQUE KEY uq_classic_round_no (presented_question_id,round_no),
 KEY ix_classic_round_attempt_status (attempt_id,status),
 CONSTRAINT fk_classic_round_attempt FOREIGN KEY (attempt_id) REFERENCES classic_exam_attempts(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_round_presented FOREIGN KEY (presented_question_id) REFERENCES classic_exam_presented_questions(id) ON DELETE CASCADE,
 CONSTRAINT ck_classic_round_status CHECK (status IN ('open','disputed','consensus','voided')),
 CONSTRAINT ck_classic_round_consensus CHECK (consensus_value IS NULL OR consensus_value IN (0,0.5,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"classic_exam_votes","kind":"create","table":"classic_exam_votes","columns":{"id":{"type":"bigint","nullable":false},"round_id":{"type":"bigint","nullable":false},"examinator_id":{"type":"int","nullable":false},"value":{"type":"decimal(2,1)","nullable":false},"created_at":{"type":"datetime(6)","nullable":false}},"constraints":["fk_classic_vote_round","fk_classic_vote_examinator","ck_classic_vote_value"],"indexes":["uq_classic_vote_member"],"requires":[]}
CREATE TABLE classic_exam_votes (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 round_id BIGINT NOT NULL,
 examinator_id INT NOT NULL,
 value DECIMAL(2,1) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_classic_vote_member (round_id,examinator_id),
 CONSTRAINT fk_classic_vote_round FOREIGN KEY (round_id) REFERENCES classic_exam_vote_rounds(id) ON DELETE CASCADE,
 CONSTRAINT fk_classic_vote_examinator FOREIGN KEY (examinator_id) REFERENCES examinators(id) ON DELETE RESTRICT,
 CONSTRAINT ck_classic_vote_value CHECK (value IN (0,0.5,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- @step {"id":"presented_consensus","kind":"add_columns","table":"classic_exam_presented_questions","columns":{"consensus_value":{"type":"decimal(2,1)","nullable":true},"weighted_score":{"type":"decimal(12,1)","nullable":true},"consensus_at":{"type":"datetime(6)","nullable":true}},"requires":[]}
ALTER TABLE classic_exam_presented_questions
 ADD COLUMN consensus_value DECIMAL(2,1) NULL,
 ADD COLUMN  weighted_score DECIMAL(12,1) NULL,
 ADD COLUMN  consensus_at DATETIME(6) NULL;
