-- Repeatable preflight. Unexpected duplicates stop the migration; nothing is merged.
DROP PROCEDURE IF EXISTS migrate_homework_unique_preflight;
DELIMITER //
CREATE PROCEDURE migrate_homework_unique_preflight()
BEGIN
  IF EXISTS (
    SELECT 1 FROM homework_sessions GROUP BY homework_id,student_id HAVING COUNT(*)>1
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='duplicate homework_sessions; run the preflight report';
  END IF;
  IF EXISTS (
    SELECT 1 FROM proctors WHERE group_id IS NOT NULL GROUP BY group_id HAVING COUNT(*)>1
  ) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='duplicate proctors.group_id; run the preflight report';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE()
      AND table_name='homework_sessions' AND index_name='uq_homework_session_pair'
  ) THEN
    ALTER TABLE homework_sessions ADD UNIQUE KEY uq_homework_session_pair (homework_id,student_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE()
      AND table_name='proctors' AND index_name='uq_proctor_group'
  ) THEN
    ALTER TABLE proctors ADD UNIQUE KEY uq_proctor_group (group_id);
  END IF;
END//
DELIMITER ;
CALL migrate_homework_unique_preflight();
DROP PROCEDURE migrate_homework_unique_preflight;

-- Diagnostic report to run if preflight stopped:
SELECT homework_id,student_id,COUNT(*) duplicate_count FROM homework_sessions
GROUP BY homework_id,student_id HAVING COUNT(*)>1;
SELECT group_id,COUNT(*) duplicate_count FROM proctors WHERE group_id IS NOT NULL
GROUP BY group_id HAVING COUNT(*)>1;

CREATE TABLE IF NOT EXISTS homework_submissions (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, homework_id INT NOT NULL, student_id INT NOT NULL,
 state VARCHAR(32) NOT NULL DEFAULT 'none', draft_file_id BIGINT UNSIGNED NULL,
 current_file_id BIGINT UNSIGNED NULL, reviewer_role VARCHAR(16) NULL, reviewer_id INT NULL,
 submitted_at_utc DATETIME(6) NULL, revision_count INT UNSIGNED NOT NULL DEFAULT 0,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_submission_pair (homework_id, student_id),
 KEY ix_hw_submission_queue (state, submitted_at_utc), KEY ix_hw_submission_reviewer (reviewer_role, reviewer_id, state),
 KEY ix_hw_submission_student (student_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS homework_submission_files (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, submission_id BIGINT UNSIGNED NOT NULL,
 object_key VARCHAR(512) NOT NULL, status VARCHAR(24) NOT NULL, size_bytes BIGINT UNSIGNED NOT NULL,
 page_count SMALLINT UNSIGNED NOT NULL, sha256 CHAR(64) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_file_key (object_key), KEY ix_hw_files_submission (submission_id, status),
 CONSTRAINT fk_hw_files_submission FOREIGN KEY (submission_id) REFERENCES homework_submissions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS homework_file_jobs (
 id CHAR(36) PRIMARY KEY, client_upload_id CHAR(36) NOT NULL, submission_id BIGINT UNSIGNED NOT NULL,
 homework_id INT NOT NULL, student_id INT NOT NULL, kind VARCHAR(24) NOT NULL DEFAULT 'process_upload',
 status VARCHAR(24) NOT NULL DEFAULT 'queued', stage VARCHAR(24) NOT NULL DEFAULT 'upload',
 progress TINYINT UNSIGNED NOT NULL DEFAULT 0, staging_key VARCHAR(512) NULL,
 source_size_bytes BIGINT UNSIGNED NULL, result_file_id BIGINT UNSIGNED NULL,
 attempts TINYINT UNSIGNED NOT NULL DEFAULT 0, manual_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
 available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), lease_owner VARCHAR(128) NULL,
 lease_expires_at DATETIME(6) NULL, heartbeat_at DATETIME(6) NULL, error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_job_client (student_id, client_upload_id), KEY ix_hw_jobs_claim (status, available_at, lease_expires_at),
 KEY ix_hw_jobs_submission (submission_id, created_at),
 CONSTRAINT fk_hw_jobs_submission FOREIGN KEY (submission_id) REFERENCES homework_submissions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS homework_s3_delete_queue (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, object_key VARCHAR(512) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'queued', attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
 available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_s3_delete_key (object_key), KEY ix_hw_s3_delete_claim (status,available_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS homework_chat_threads (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, homework_id INT NOT NULL, student_id INT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'active', created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_thread_pair (homework_id, student_id), KEY ix_hw_threads_inbox (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS homework_chat_messages (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, thread_id BIGINT UNSIGNED NOT NULL,
 client_message_id CHAR(36) NULL, sender_role VARCHAR(16) NOT NULL, sender_id INT NULL,
 kind VARCHAR(16) NOT NULL DEFAULT 'user', body VARCHAR(1000) NULL, event_code VARCHAR(48) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_hw_message_client (thread_id, client_message_id), KEY ix_hw_messages_history (thread_id, id),
 CONSTRAINT fk_hw_messages_thread FOREIGN KEY (thread_id) REFERENCES homework_chat_threads(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS homework_chat_reads (
 thread_id BIGINT UNSIGNED NOT NULL, reader_role VARCHAR(16) NOT NULL, reader_id INT NOT NULL,
 last_message_id BIGINT UNSIGNED NOT NULL DEFAULT 0, updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 PRIMARY KEY (thread_id, reader_role, reader_id), KEY ix_hw_reads (reader_role, reader_id, last_message_id),
 CONSTRAINT fk_hw_reads_thread FOREIGN KEY (thread_id) REFERENCES homework_chat_threads(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS homework_chat_admin_followers (
 thread_id BIGINT UNSIGNED NOT NULL, admin_id INT NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY (thread_id, admin_id), KEY ix_hw_followers_admin (admin_id, created_at),
 CONSTRAINT fk_hw_followers_thread FOREIGN KEY (thread_id) REFERENCES homework_chat_threads(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS homework_realtime_outbox (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, thread_id BIGINT UNSIGNED NULL, event_type VARCHAR(48) NOT NULL,
 entity_id BIGINT UNSIGNED NULL, payload_json JSON NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 expires_at DATETIME(6) NOT NULL, KEY ix_hw_outbox_poll (id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS homework_realtime_presence (
 thread_id BIGINT UNSIGNED NOT NULL, actor_role VARCHAR(16) NOT NULL, actor_id INT NOT NULL,
 is_typing TINYINT(1) NOT NULL DEFAULT 0, expires_at DATETIME(6) NOT NULL,
 PRIMARY KEY (thread_id, actor_role, actor_id), KEY ix_hw_presence_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS notifications (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, recipient_role VARCHAR(16) NOT NULL, recipient_id INT NOT NULL,
 kind VARCHAR(48) NOT NULL, homework_id INT NULL, student_id INT NULL, thread_id BIGINT UNSIGNED NULL,
 read_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 KEY ix_notifications_inbox (recipient_role, recipient_id, read_at, created_at), KEY ix_notifications_expiry (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS push_subscriptions (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, user_role VARCHAR(16) NOT NULL, user_id INT NOT NULL,
 endpoint_hash CHAR(64) NOT NULL, endpoint TEXT NOT NULL, p256dh VARCHAR(255) NOT NULL, auth_secret VARCHAR(255) NOT NULL,
 enabled TINYINT(1) NOT NULL DEFAULT 1, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_push_endpoint (endpoint_hash), KEY ix_push_user (user_role, user_id, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS application_settings (
 setting_key VARCHAR(100) PRIMARY KEY, setting_value VARCHAR(1000) NOT NULL, updated_by INT NULL,
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
INSERT INTO application_settings (setting_key, setting_value) VALUES ('chat_messages_per_minute', '20')
ON DUPLICATE KEY UPDATE setting_key=VALUES(setting_key);
