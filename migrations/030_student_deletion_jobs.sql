-- Additive only. Apply explicitly after all existing backend migrations.
-- Never removes student data. Deploy all services with the feature disabled first.
CREATE TABLE IF NOT EXISTS student_deletion_jobs (
 id CHAR(36) NOT NULL PRIMARY KEY,
 student_id BIGINT NOT NULL,
 actor_role VARCHAR(20) NOT NULL,
 actor_id BIGINT NOT NULL,
 idempotency_key CHAR(36) NOT NULL,
 confirmation_hash CHAR(64) NOT NULL,
 fingerprint CHAR(64) NOT NULL,
 student_json JSON NOT NULL,
 manifest_json JSON NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'queued',
 stage VARCHAR(32) NOT NULL DEFAULT 'homework',
 completed_units BIGINT UNSIGNED NOT NULL DEFAULT 0,
 total_units BIGINT UNSIGNED NOT NULL,
 lease_owner CHAR(36) NULL,
 lease_expires_at DATETIME(6) NULL,
 available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 attempts INT UNSIGNED NOT NULL DEFAULT 0,
 error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 completed_at DATETIME(6) NULL,
 UNIQUE KEY uq_student_deletion_student (student_id),
 UNIQUE KEY uq_student_deletion_request (actor_role,actor_id,idempotency_key),
 KEY ix_student_deletion_dispatch (status,available_at,lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
-- Intentionally no FK: this non-content tombstone must survive account erasure.
CREATE TABLE IF NOT EXISTS student_deletion_barriers (
 student_id BIGINT NOT NULL PRIMARY KEY,
 job_id CHAR(36) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_student_deletion_barrier_job (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
