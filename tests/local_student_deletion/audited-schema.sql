-- Read-only SHOW CREATE TABLE snapshot; do not execute as a migration.
-- AUTO_INCREMENT counters omitted; account definers anonymized if present.

-- Allratings
CREATE TABLE `Allratings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `exams` float NOT NULL,
  `homework` float NOT NULL,
  `tests` float NOT NULL,
  `final` float NOT NULL,
  `details_json` json DEFAULT NULL,
  `calculation_job_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_allratings_student` (`student_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- admin_role_permissions
CREATE TABLE `admin_role_permissions` (
  `role_id` int NOT NULL,
  `section` varchar(32) NOT NULL,
  `can_view` tinyint(1) NOT NULL DEFAULT '0',
  `can_edit` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`role_id`,`section`),
  CONSTRAINT `fk_admin_permission_role` FOREIGN KEY (`role_id`) REFERENCES `admin_roles` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_admin_permission_flags` CHECK (((`can_view` in (0,1)) and (`can_edit` in (0,1)) and (`can_edit` <= `can_view`)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- admin_role_users
CREATE TABLE `admin_role_users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(100) NOT NULL,
  `role_id` int NOT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `session_version` int unsigned NOT NULL DEFAULT '1',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_admin_role_users_role` (`role_id`),
  CONSTRAINT `fk_admin_role_user_role` FOREIGN KEY (`role_id`) REFERENCES `admin_roles` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_admin_role_user_active` CHECK ((`is_active` in (0,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- admin_roles
CREATE TABLE `admin_roles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `description` varchar(500) NOT NULL DEFAULT '',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_admin_role_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- admins
CREATE TABLE `admins` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- application_settings
CREATE TABLE `application_settings` (
  `setting_key` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `setting_value` varchar(1000) COLLATE utf8mb4_unicode_ci NOT NULL,
  `updated_by` int DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`setting_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- attendance
CREATE TABLE `attendance` (
  `id` int NOT NULL AUTO_INCREMENT,
  `date` date NOT NULL,
  `student_id` int NOT NULL,
  `attendance_rate` int NOT NULL DEFAULT '1',
  `zap_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `attendance_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- attendance_types
CREATE TABLE `attendance_types` (
  `id` tinyint unsigned NOT NULL,
  `code` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Код для API/логики',
  `name_ru` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Название для отображения',
  `sort_order` tinyint unsigned NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- auth_users
CREATE TABLE `auth_users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(50) NOT NULL,
  `ref_id` int NOT NULL,
  `password` varchar(255) DEFAULT NULL,
  `role` enum('student','proctor','admin','examinator','supervisor','staff_admin') NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- card_themes
CREATE TABLE `card_themes` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `direction_id` int NOT NULL COMMENT 'FK directions.id',
  PRIMARY KEY (`id`),
  KEY `idx_card_themes_direction_id` (`direction_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- cards
CREATE TABLE `cards` (
  `id` int NOT NULL AUTO_INCREMENT,
  `question` text NOT NULL,
  `answer` text NOT NULL,
  `theme_id` int NOT NULL,
  `sort_order` int NOT NULL DEFAULT '0' COMMENT 'Порядок в разделе',
  PRIMARY KEY (`id`),
  KEY `theme_id` (`theme_id`),
  CONSTRAINT `cards_ibfk_1` FOREIGN KEY (`theme_id`) REFERENCES `card_themes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- class_day_attendance
CREATE TABLE `class_day_attendance` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `class_day_id` int unsigned NOT NULL,
  `student_id` int NOT NULL,
  `attendance_type_id` tinyint unsigned NOT NULL,
  `zap_id` int DEFAULT NULL COMMENT 'Связь с отгулом для типов по уважительной причине',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_class_day_student` (`class_day_id`,`student_id`),
  KEY `fk_cda_student` (`student_id`),
  KEY `fk_cda_type` (`attendance_type_id`),
  KEY `fk_cda_zap` (`zap_id`),
  CONSTRAINT `fk_cda_class_day` FOREIGN KEY (`class_day_id`) REFERENCES `class_days` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_cda_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_cda_type` FOREIGN KEY (`attendance_type_id`) REFERENCES `attendance_types` (`id`),
  CONSTRAINT `fk_cda_zap` FOREIGN KEY (`zap_id`) REFERENCES `zaps` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- class_days
CREATE TABLE `class_days` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `date` date NOT NULL COMMENT 'Дата занятия',
  `comment` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Комментарий (опционально)',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_class_days_date` (`date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_appeals
CREATE TABLE `classic_exam_appeals` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `attempt_id` bigint NOT NULL,
  `previous_grade` tinyint unsigned NOT NULL,
  `new_grade` tinyint unsigned NOT NULL,
  `changed_by_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `changed_by_admin_id` int NOT NULL,
  `admin_name_snapshot` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `ix_classic_appeal_attempt` (`attempt_id`,`id`),
  CONSTRAINT `fk_classic_appeal_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_appeal_actor` CHECK ((`changed_by_role` in (_utf8mb4'admin',_utf8mb4'staff_admin'))),
  CONSTRAINT `ck_classic_appeal_new` CHECK ((`new_grade` between 0 and 5)),
  CONSTRAINT `ck_classic_appeal_previous` CHECK ((`previous_grade` between 0 and 5))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_assignment_import_sessions
CREATE TABLE `classic_exam_assignment_import_sessions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `created_by_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_by` int NOT NULL,
  `source_filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `preview_payload` json NOT NULL,
  `preview_version` bigint unsigned NOT NULL DEFAULT '1',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'editable',
  `commit_result` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  `committed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_assignment_import_owner` (`exam_id`,`created_by_role`,`created_by`,`expires_at`),
  KEY `ix_assignment_import_expiry` (`expires_at`,`id`,`exam_id`),
  CONSTRAINT `fk_assignment_import_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_assignment_import_role` CHECK ((`created_by_role` in (_utf8mb4'admin',_utf8mb4'staff_admin'))),
  CONSTRAINT `ck_assignment_import_status` CHECK ((`status` in (_utf8mb4'editable',_utf8mb4'committed')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_assignment_members
CREATE TABLE `classic_exam_assignment_members` (
  `assignment_id` bigint NOT NULL,
  `examinator_id` int NOT NULL,
  `position` smallint unsigned NOT NULL,
  `full_name_snapshot` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`assignment_id`,`examinator_id`),
  UNIQUE KEY `uq_classic_am_position` (`assignment_id`,`position`),
  KEY `fk_classic_am_examinator` (`examinator_id`),
  CONSTRAINT `fk_classic_am_assignment` FOREIGN KEY (`assignment_id`) REFERENCES `classic_exam_assignments` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_am_examinator` FOREIGN KEY (`examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_am_position` CHECK ((`position` between 1 and 6))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_assignments
CREATE TABLE `classic_exam_assignments` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `student_id` int NOT NULL,
  `attempt_no` tinyint unsigned NOT NULL DEFAULT '1',
  `source_commission_id` bigint DEFAULT NULL,
  `created_by_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_by` int NOT NULL,
  `commission_name_snapshot` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `history_generation` bigint unsigned NOT NULL DEFAULT '1',
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_assignment_attempt` (`exam_id`,`student_id`,`attempt_no`),
  KEY `fk_classic_assignment_student` (`student_id`),
  KEY `fk_classic_assignment_commission` (`source_commission_id`),
  CONSTRAINT `fk_classic_assignment_commission` FOREIGN KEY (`source_commission_id`) REFERENCES `classic_exam_commissions` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_classic_assignment_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_assignment_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_assignment_actor` CHECK ((`created_by_role` in (_utf8mb4'admin',_utf8mb4'staff_admin'))),
  CONSTRAINT `ck_classic_assignment_attempt` CHECK ((`attempt_no` in (1,2)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_attempt_commands
CREATE TABLE `classic_exam_attempt_commands` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `attempt_id` bigint NOT NULL,
  `actor_examinator_id` int NOT NULL,
  `idempotency_key` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `command_type` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `receipt` json NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_command_actor_key` (`attempt_id`,`actor_examinator_id`,`idempotency_key`),
  KEY `fk_classic_command_examinator` (`actor_examinator_id`),
  CONSTRAINT `fk_classic_command_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_command_examinator` FOREIGN KEY (`actor_examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_attempt_members
CREATE TABLE `classic_exam_attempt_members` (
  `attempt_id` bigint NOT NULL,
  `examinator_id` int NOT NULL,
  `full_name_snapshot` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `position` tinyint unsigned NOT NULL,
  `ready_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`attempt_id`,`examinator_id`),
  UNIQUE KEY `uq_classic_atm_position` (`attempt_id`,`position`),
  KEY `fk_classic_atm_examinator` (`examinator_id`),
  CONSTRAINT `fk_classic_atm_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_atm_examinator` FOREIGN KEY (`examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_atm_position` CHECK ((`position` between 1 and 6))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_attempt_part_progress
CREATE TABLE `classic_exam_attempt_part_progress` (
  `attempt_id` bigint NOT NULL,
  `source_part_id` bigint NOT NULL,
  `part_code` char(1) COLLATE utf8mb4_unicode_ci NOT NULL,
  `question_weight` int unsigned NOT NULL,
  `required_count` int unsigned NOT NULL,
  `consensus_count` int unsigned NOT NULL DEFAULT '0',
  `cycle_no` int unsigned NOT NULL DEFAULT '1',
  PRIMARY KEY (`attempt_id`,`source_part_id`),
  CONSTRAINT `fk_classic_progress_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_progress_count` CHECK ((`consensus_count` <= `required_count`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_attempt_question_usage
CREATE TABLE `classic_exam_attempt_question_usage` (
  `attempt_id` bigint NOT NULL,
  `definition_question_id` bigint NOT NULL,
  `last_cycle_no` int unsigned NOT NULL,
  `ever_presented` tinyint(1) NOT NULL DEFAULT '0',
  `permanently_excluded` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`attempt_id`,`definition_question_id`),
  KEY `fk_classic_usage_definition` (`definition_question_id`),
  CONSTRAINT `fk_classic_usage_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_usage_definition` FOREIGN KEY (`definition_question_id`) REFERENCES `classic_exam_definition_questions` (`id`) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_attempts
CREATE TABLE `classic_exam_attempts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `student_id` int NOT NULL,
  `assignment_id` bigint NOT NULL,
  `definition_id` bigint DEFAULT NULL,
  `attempt_no` tinyint unsigned NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending_ready',
  `phase` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'preparation',
  `state_version` bigint unsigned NOT NULL DEFAULT '1',
  `history_generation` bigint unsigned NOT NULL,
  `student_name_snapshot` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `replacement_limit_snapshot` int unsigned DEFAULT NULL,
  `replacement_used` int unsigned NOT NULL DEFAULT '0',
  `started_at` datetime(6) DEFAULT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `current_presented_question_id` bigint DEFAULT NULL,
  `last_definition_question_id` bigint DEFAULT NULL,
  `raw_total` decimal(12,1) DEFAULT NULL,
  `rounded_total` int unsigned DEFAULT NULL,
  `max_score` int unsigned DEFAULT NULL,
  `calculated_grade` tinyint unsigned DEFAULT NULL,
  `resolution_type` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `result_version` bigint unsigned NOT NULL DEFAULT '1',
  `published_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_attempt_student_no` (`exam_id`,`student_id`,`attempt_no`),
  UNIQUE KEY `uq_classic_attempt_assignment` (`assignment_id`),
  KEY `ix_classic_attempt_exam_status` (`exam_id`,`status`,`id`),
  KEY `ix_classic_attempt_student_exam` (`student_id`,`exam_id`,`id`),
  KEY `fk_classic_attempt_definition` (`definition_id`),
  CONSTRAINT `fk_classic_attempt_assignment` FOREIGN KEY (`assignment_id`) REFERENCES `classic_exam_assignments` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_classic_attempt_definition` FOREIGN KEY (`definition_id`) REFERENCES `classic_exam_definition_versions` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_classic_attempt_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_attempt_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_attempt_grade` CHECK (((`calculated_grade` is null) or (`calculated_grade` between 0 and 5))),
  CONSTRAINT `ck_classic_attempt_no` CHECK ((`attempt_no` in (1,2))),
  CONSTRAINT `ck_classic_attempt_phase` CHECK ((`phase` in (_utf8mb4'preparation',_utf8mb4'regular_questions',_utf8mb4'tie_breaker',_utf8mb4'completed'))),
  CONSTRAINT `ck_classic_attempt_replacements` CHECK (((`replacement_limit_snapshot` is null) or (`replacement_used` <= `replacement_limit_snapshot`))),
  CONSTRAINT `ck_classic_attempt_status` CHECK ((`status` in (_utf8mb4'pending_ready',_utf8mb4'in_progress',_utf8mb4'completed')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_commission_members
CREATE TABLE `classic_exam_commission_members` (
  `commission_id` bigint NOT NULL,
  `examinator_id` int NOT NULL,
  `position` smallint unsigned NOT NULL,
  PRIMARY KEY (`commission_id`,`examinator_id`),
  UNIQUE KEY `uq_classic_cm_position` (`commission_id`,`position`),
  KEY `fk_classic_cm_examinator` (`examinator_id`),
  CONSTRAINT `fk_classic_cm_commission` FOREIGN KEY (`commission_id`) REFERENCES `classic_exam_commissions` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_cm_examinator` FOREIGN KEY (`examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_cm_position` CHECK ((`position` between 1 and 6))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_commissions
CREATE TABLE `classic_exam_commissions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `name` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_commission_name` (`exam_id`,`name`),
  CONSTRAINT `fk_classic_commission_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_definition_questions
CREATE TABLE `classic_exam_definition_questions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `definition_id` bigint NOT NULL,
  `source_question_id` bigint NOT NULL,
  `source_part_id` bigint NOT NULL,
  `part_code` char(1) COLLATE utf8mb4_unicode_ci NOT NULL,
  `question_text` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_text` text COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_dq_source` (`definition_id`,`source_question_id`),
  KEY `ix_classic_dq_part` (`definition_id`,`source_part_id`,`id`),
  CONSTRAINT `fk_classic_dq_definition` FOREIGN KEY (`definition_id`) REFERENCES `classic_exam_definition_versions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_definition_versions
CREATE TABLE `classic_exam_definition_versions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `config_version` bigint unsigned NOT NULL,
  `config_json` json NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_definition_version` (`exam_id`,`config_version`),
  CONSTRAINT `fk_classic_definition_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_grade_thresholds
CREATE TABLE `classic_exam_grade_thresholds` (
  `exam_id` int NOT NULL,
  `grade` tinyint unsigned NOT NULL,
  `min_score` int unsigned NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`exam_id`,`grade`),
  CONSTRAINT `fk_classic_threshold_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_threshold_grade` CHECK ((`grade` between 0 and 5)),
  CONSTRAINT `ck_classic_threshold_zero` CHECK (((`grade` <> 0) or (`min_score` = 0)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_parts
CREATE TABLE `classic_exam_parts` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `code` char(1) COLLATE utf8mb4_unicode_ci NOT NULL,
  `question_weight` int unsigned DEFAULT NULL,
  `question_count` int unsigned DEFAULT NULL,
  `sort_order` int unsigned NOT NULL,
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_part_code` (`exam_id`,`code`),
  UNIQUE KEY `uq_classic_part_order` (`exam_id`,`sort_order`),
  CONSTRAINT `fk_classic_part_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_part_code` CHECK ((ascii(`code`) between 65 and 90)),
  CONSTRAINT `ck_classic_part_count` CHECK (((`question_count` is null) or (`question_count` between 1 and 100))),
  CONSTRAINT `ck_classic_part_weight` CHECK (((`question_weight` is null) or (`question_weight` between 1 and 1000)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_presented_questions
CREATE TABLE `classic_exam_presented_questions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `attempt_id` bigint NOT NULL,
  `definition_question_id` bigint NOT NULL,
  `sequence_no` bigint unsigned NOT NULL,
  `purpose` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `cycle_no` int unsigned NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'open',
  `replaces_presented_question_id` bigint DEFAULT NULL,
  `replaced_by_examinator_id` int DEFAULT NULL,
  `replaced_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `consensus_value` decimal(2,1) DEFAULT NULL,
  `weighted_score` decimal(12,1) DEFAULT NULL,
  `consensus_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_presented_sequence` (`attempt_id`,`sequence_no`),
  KEY `ix_classic_presented_cycle` (`attempt_id`,`definition_question_id`,`cycle_no`),
  KEY `ix_classic_presented_status` (`attempt_id`,`status`,`purpose`),
  KEY `fk_classic_presented_definition` (`definition_question_id`),
  KEY `fk_classic_presented_replacer` (`replaced_by_examinator_id`),
  CONSTRAINT `fk_classic_presented_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_presented_definition` FOREIGN KEY (`definition_question_id`) REFERENCES `classic_exam_definition_questions` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_classic_presented_replacer` FOREIGN KEY (`replaced_by_examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_presented_purpose` CHECK ((`purpose` in (_utf8mb4'regular',_utf8mb4'tie_breaker'))),
  CONSTRAINT `ck_classic_presented_status` CHECK ((`status` in (_utf8mb4'open',_utf8mb4'replaced',_utf8mb4'consensus')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_question_import_sessions
CREATE TABLE `classic_exam_question_import_sessions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `created_by_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_by` int NOT NULL,
  `source_filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `preview_payload` json NOT NULL,
  `preview_version` bigint unsigned NOT NULL DEFAULT '1',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'editable',
  `commit_result` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  `committed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_question_import_owner` (`exam_id`,`created_by_role`,`created_by`,`expires_at`),
  KEY `ix_question_import_expiry` (`expires_at`,`id`,`exam_id`),
  CONSTRAINT `fk_question_import_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_question_import_role` CHECK ((`created_by_role` in (_utf8mb4'admin',_utf8mb4'staff_admin'))),
  CONSTRAINT `ck_question_import_status` CHECK ((`status` in (_utf8mb4'editable',_utf8mb4'committed')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_questions
CREATE TABLE `classic_exam_questions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `part_id` bigint NOT NULL,
  `question_text` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `answer_text` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `content_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sort_order` int unsigned NOT NULL,
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_question_content` (`part_id`,`content_hash`),
  UNIQUE KEY `uq_classic_question_order` (`part_id`,`sort_order`),
  KEY `ix_classic_question_exam_part` (`exam_id`,`part_id`),
  CONSTRAINT `fk_classic_question_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_question_part` FOREIGN KEY (`part_id`) REFERENCES `classic_exam_parts` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_settings
CREATE TABLE `classic_exam_settings` (
  `exam_id` int NOT NULL,
  `start_at` datetime(6) DEFAULT NULL,
  `end_at` datetime(6) DEFAULT NULL,
  `fractional_mode` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `tie_breaker_source_mode` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `tie_breaker_part_id` bigint DEFAULT NULL,
  `tie_breaker_half_mode` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `config_version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`exam_id`),
  KEY `ix_classic_settings_start` (`start_at`,`exam_id`),
  KEY `fk_classic_settings_part` (`tie_breaker_part_id`),
  CONSTRAINT `fk_classic_settings_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_settings_part` FOREIGN KEY (`tie_breaker_part_id`) REFERENCES `classic_exam_parts` (`id`) ON DELETE SET NULL,
  CONSTRAINT `ck_classic_settings_fraction` CHECK (((`fractional_mode` is null) or (`fractional_mode` in (_utf8mb4'round_up',_utf8mb4'round_down',_utf8mb4'extra_question')))),
  CONSTRAINT `ck_classic_settings_half` CHECK (((`tie_breaker_half_mode` is null) or (`tie_breaker_half_mode` in (_utf8mb4'repeat',_utf8mb4'round_up',_utf8mb4'round_down')))),
  CONSTRAINT `ck_classic_settings_source` CHECK (((`tie_breaker_source_mode` is null) or (`tie_breaker_source_mode` in (_utf8mb4'specific_part',_utf8mb4'any_part')))),
  CONSTRAINT `ck_classic_settings_window` CHECK (((`end_at` is null) or (`start_at` is null) or (`end_at` > `start_at`)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_student_privileges
CREATE TABLE `classic_exam_student_privileges` (
  `exam_id` int NOT NULL,
  `student_id` int NOT NULL,
  `replacement_limit` int unsigned NOT NULL DEFAULT '0',
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`exam_id`,`student_id`),
  KEY `fk_classic_privilege_student` (`student_id`),
  CONSTRAINT `fk_classic_privilege_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_privilege_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_classic_privilege_limit` CHECK ((`replacement_limit` between 0 and 100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_vote_rounds
CREATE TABLE `classic_exam_vote_rounds` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `attempt_id` bigint NOT NULL,
  `presented_question_id` bigint NOT NULL,
  `round_no` int unsigned NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'open',
  `consensus_value` decimal(2,1) DEFAULT NULL,
  `opened_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `completed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_round_no` (`presented_question_id`,`round_no`),
  KEY `ix_classic_round_attempt_status` (`attempt_id`,`status`),
  CONSTRAINT `fk_classic_round_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `classic_exam_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_classic_round_presented` FOREIGN KEY (`presented_question_id`) REFERENCES `classic_exam_presented_questions` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_round_consensus` CHECK (((`consensus_value` is null) or (`consensus_value` in (0,0.5,1)))),
  CONSTRAINT `ck_classic_round_status` CHECK ((`status` in (_utf8mb4'open',_utf8mb4'disputed',_utf8mb4'consensus',_utf8mb4'voided')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- classic_exam_votes
CREATE TABLE `classic_exam_votes` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `round_id` bigint NOT NULL,
  `examinator_id` int NOT NULL,
  `value` decimal(2,1) NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_classic_vote_member` (`round_id`,`examinator_id`),
  KEY `fk_classic_vote_examinator` (`examinator_id`),
  CONSTRAINT `fk_classic_vote_examinator` FOREIGN KEY (`examinator_id`) REFERENCES `examinators` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_classic_vote_round` FOREIGN KEY (`round_id`) REFERENCES `classic_exam_vote_rounds` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_classic_vote_value` CHECK ((`value` in (0,0.5,1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- directions
CREATE TABLE `directions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(200) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- exam_admin_commands
CREATE TABLE `exam_admin_commands` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `actor_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `actor_id` int NOT NULL,
  `idempotency_key` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `command_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `target_exam_id` int DEFAULT NULL,
  `target_student_id` int DEFAULT NULL,
  `target_attempt_id` bigint DEFAULT NULL,
  `request_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `receipt` json DEFAULT NULL,
  `tombstone` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_eac_actor_key` (`actor_role`,`actor_id`,`idempotency_key`),
  KEY `ix_eac_exam_expiry` (`target_exam_id`,`expires_at`),
  KEY `ix_admin_command_expiry` (`expires_at`,`id`),
  CONSTRAINT `ck_eac_actor_role` CHECK ((`actor_role` in (_utf8mb4'admin',_utf8mb4'staff_admin')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- exam_schema_migration_steps
CREATE TABLE `exam_schema_migration_steps` (
  `migration` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `step_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_checksum` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `step_checksum` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `started_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `completed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`migration`,`step_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- exam_sessions
CREATE TABLE `exam_sessions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `student_id` int NOT NULL,
  `val` decimal(12,2) NOT NULL,
  `points` tinyint unsigned NOT NULL,
  `examinator` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_exam_sessions_exam_student` (`exam_id`,`student_id`),
  KEY `idx_exam_sessions_exam_id` (`exam_id`),
  KEY `idx_exam_sessions_student_id` (`student_id`),
  CONSTRAINT `fk_exam_sessions_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_exam_sessions_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_exam_sessions_grade` CHECK (((`points` between 0 and 5) and (`points` = floor(`points`)))),
  CONSTRAINT `ck_exam_sessions_nonnegative` CHECK (((`val` between 0 and 9999999999.99) and (`val` = round(`val`,2))))
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- examinators
CREATE TABLE `examinators` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- exams
CREATE TABLE `exams` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(60) DEFAULT NULL,
  `date` date DEFAULT NULL,
  `exam_type` varchar(20) NOT NULL DEFAULT 'outside_lms',
  `direction_id` int NOT NULL,
  `version` bigint unsigned NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `training_enabled` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_exams_date` (`date`),
  KEY `idx_exams_type_date` (`exam_type`,`date`),
  KEY `idx_exams_direction` (`direction_id`),
  CONSTRAINT `fk_exams_direction` FOREIGN KEY (`direction_id`) REFERENCES `directions` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `ck_exams_type` CHECK ((`exam_type` in (_utf8mb4'outside_lms',_utf8mb4'classic')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- groups
CREATE TABLE `groups` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- homework
CREATE TABLE `homework` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(50) NOT NULL,
  `type` enum('ОВ','ДЗНВ') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `deadline` date NOT NULL,
  `published` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`),
  KEY `idx_homework_published` (`published`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- homework_chat_admin_followers
CREATE TABLE `homework_chat_admin_followers` (
  `thread_id` bigint unsigned NOT NULL,
  `admin_id` int NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`thread_id`,`admin_id`),
  KEY `ix_hw_followers_admin` (`admin_id`,`created_at`),
  CONSTRAINT `fk_hw_followers_thread` FOREIGN KEY (`thread_id`) REFERENCES `homework_chat_threads` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_chat_messages
CREATE TABLE `homework_chat_messages` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `thread_id` bigint unsigned NOT NULL,
  `client_message_id` char(36) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sender_role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sender_id` int DEFAULT NULL,
  `kind` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'user',
  `body` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `event_code` varchar(48) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_message_client` (`thread_id`,`client_message_id`),
  KEY `ix_hw_messages_history` (`thread_id`,`id`),
  CONSTRAINT `fk_hw_messages_thread` FOREIGN KEY (`thread_id`) REFERENCES `homework_chat_threads` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_chat_reads
CREATE TABLE `homework_chat_reads` (
  `thread_id` bigint unsigned NOT NULL,
  `reader_role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `reader_id` int NOT NULL,
  `last_message_id` bigint unsigned NOT NULL DEFAULT '0',
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`thread_id`,`reader_role`,`reader_id`),
  KEY `ix_hw_reads` (`reader_role`,`reader_id`,`last_message_id`),
  CONSTRAINT `fk_hw_reads_thread` FOREIGN KEY (`thread_id`) REFERENCES `homework_chat_threads` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_chat_threads
CREATE TABLE `homework_chat_threads` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `homework_id` int NOT NULL,
  `student_id` int NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'active',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_thread_pair` (`homework_id`,`student_id`),
  KEY `ix_hw_threads_inbox` (`status`,`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_file_jobs
CREATE TABLE `homework_file_jobs` (
  `id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_upload_id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `submission_id` bigint unsigned NOT NULL,
  `homework_id` int NOT NULL,
  `student_id` int NOT NULL,
  `kind` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'process_upload',
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'queued',
  `stage` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'upload',
  `progress` tinyint unsigned NOT NULL DEFAULT '0',
  `staging_key` varchar(512) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `source_size_bytes` bigint unsigned DEFAULT NULL,
  `result_file_id` bigint unsigned DEFAULT NULL,
  `attempts` tinyint unsigned NOT NULL DEFAULT '0',
  `manual_attempts` tinyint unsigned NOT NULL DEFAULT '0',
  `available_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `lease_owner` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `lease_expires_at` datetime(6) DEFAULT NULL,
  `heartbeat_at` datetime(6) DEFAULT NULL,
  `error_code` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_job_client` (`student_id`,`client_upload_id`),
  KEY `ix_hw_jobs_claim` (`status`,`available_at`,`lease_expires_at`),
  KEY `ix_hw_jobs_submission` (`submission_id`,`created_at`),
  CONSTRAINT `fk_hw_jobs_submission` FOREIGN KEY (`submission_id`) REFERENCES `homework_submissions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_realtime_outbox
CREATE TABLE `homework_realtime_outbox` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `thread_id` bigint unsigned DEFAULT NULL,
  `event_type` varchar(48) COLLATE utf8mb4_unicode_ci NOT NULL,
  `entity_id` bigint unsigned DEFAULT NULL,
  `payload_json` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_hw_outbox_poll` (`id`,`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_realtime_presence
CREATE TABLE `homework_realtime_presence` (
  `thread_id` bigint unsigned NOT NULL,
  `actor_role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `actor_id` int NOT NULL,
  `is_typing` tinyint(1) NOT NULL DEFAULT '0',
  `expires_at` datetime(6) NOT NULL,
  PRIMARY KEY (`thread_id`,`actor_role`,`actor_id`),
  KEY `ix_hw_presence_expiry` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_s3_delete_queue
CREATE TABLE `homework_s3_delete_queue` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `object_key` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'queued',
  `attempts` tinyint unsigned NOT NULL DEFAULT '0',
  `available_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `error_code` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_s3_delete_key` (`object_key`),
  KEY `ix_hw_s3_delete_claim` (`status`,`available_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_sessions
CREATE TABLE `homework_sessions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `status` tinyint(1) NOT NULL,
  `result` double DEFAULT NULL,
  `homework_id` int NOT NULL,
  `student_id` int NOT NULL,
  `date_pass` date DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_homework_session_pair` (`homework_id`,`student_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `homework_sessions_ibfk_1` FOREIGN KEY (`homework_id`) REFERENCES `homework` (`id`) ON DELETE CASCADE,
  CONSTRAINT `homework_sessions_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- homework_submission_files
CREATE TABLE `homework_submission_files` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `submission_id` bigint unsigned NOT NULL,
  `object_key` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `size_bytes` bigint unsigned NOT NULL,
  `page_count` smallint unsigned NOT NULL,
  `sha256` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_file_key` (`object_key`),
  KEY `ix_hw_files_submission` (`submission_id`,`status`),
  CONSTRAINT `fk_hw_files_submission` FOREIGN KEY (`submission_id`) REFERENCES `homework_submissions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- homework_submissions
CREATE TABLE `homework_submissions` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `homework_id` int NOT NULL,
  `student_id` int NOT NULL,
  `state` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'none',
  `draft_file_id` bigint unsigned DEFAULT NULL,
  `current_file_id` bigint unsigned DEFAULT NULL,
  `reviewer_role` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reviewer_id` int DEFAULT NULL,
  `submitted_at_utc` datetime(6) DEFAULT NULL,
  `revision_count` int unsigned NOT NULL DEFAULT '0',
  `revision_comment` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hw_submission_pair` (`homework_id`,`student_id`),
  KEY `ix_hw_submission_queue` (`state`,`submitted_at_utc`),
  KEY `ix_hw_submission_reviewer` (`reviewer_role`,`reviewer_id`,`state`),
  KEY `ix_hw_submission_student` (`student_id`,`updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- notifications
CREATE TABLE `notifications` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `recipient_role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `recipient_id` int NOT NULL,
  `kind` varchar(48) COLLATE utf8mb4_unicode_ci NOT NULL,
  `homework_id` int DEFAULT NULL,
  `student_id` int DEFAULT NULL,
  `thread_id` bigint unsigned DEFAULT NULL,
  `read_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `ix_notifications_inbox` (`recipient_role`,`recipient_id`,`read_at`,`created_at`),
  KEY `ix_notifications_expiry` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- outside_exam_result_import_sessions
CREATE TABLE `outside_exam_result_import_sessions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `exam_id` int NOT NULL,
  `created_by_role` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_by` int NOT NULL,
  `source_filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `preview_payload` json NOT NULL,
  `preview_version` bigint unsigned NOT NULL DEFAULT '1',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'editable',
  `commit_result` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  `committed_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_outside_import_owner` (`exam_id`,`created_by_role`,`created_by`,`expires_at`),
  KEY `ix_outside_import_expiry` (`expires_at`,`id`,`exam_id`),
  CONSTRAINT `fk_outside_import_exam` FOREIGN KEY (`exam_id`) REFERENCES `exams` (`id`) ON DELETE CASCADE,
  CONSTRAINT `ck_outside_import_role` CHECK ((`created_by_role` in (_utf8mb4'admin',_utf8mb4'staff_admin'))),
  CONSTRAINT `ck_outside_import_status` CHECK ((`status` in (_utf8mb4'editable',_utf8mb4'committed')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- proctors
CREATE TABLE `proctors` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(50) NOT NULL,
  `group_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_proctor_group` (`group_id`),
  KEY `idx_proctors_group_id` (`group_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- push_subscriptions
CREATE TABLE `push_subscriptions` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_role` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` int NOT NULL,
  `endpoint_hash` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `endpoint` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `p256dh` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `auth_secret` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_push_endpoint` (`endpoint_hash`),
  KEY `ix_push_user` (`user_role`,`user_id`,`enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- rating_recalc_jobs
CREATE TABLE `rating_recalc_jobs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `status` enum('queued','running','completed','failed') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'queued',
  `date_from` date NOT NULL,
  `date_to` date NOT NULL,
  `created_by` int DEFAULT NULL COMMENT 'ID пользователя из JWT',
  `created_by_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `total_students` int NOT NULL DEFAULT '0',
  `processed_count` int NOT NULL DEFAULT '0',
  `successful` int NOT NULL DEFAULT '0',
  `failed` int NOT NULL DEFAULT '0',
  `skipped` int NOT NULL DEFAULT '0',
  `message` text COLLATE utf8mb4_unicode_ci,
  `errors` json DEFAULT NULL,
  `exam_source_revision` bigint unsigned DEFAULT NULL,
  `as_of` datetime(6) DEFAULT NULL,
  `worker_token` char(36) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `heartbeat_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_rating_recalc_status` (`status`),
  KEY `idx_rating_recalc_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- rating_recalc_staging
CREATE TABLE `rating_recalc_staging` (
  `job_id` int NOT NULL,
  `student_id` int NOT NULL,
  `exams` float NOT NULL,
  `homework` float NOT NULL,
  `tests` float NOT NULL,
  `final` float NOT NULL,
  `details_json` json NOT NULL,
  PRIMARY KEY (`job_id`,`student_id`),
  CONSTRAINT `fk_rating_staging_job` FOREIGN KEY (`job_id`) REFERENCES `rating_recalc_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- rating_source_state
CREATE TABLE `rating_source_state` (
  `id` tinyint NOT NULL,
  `source_revision` bigint unsigned NOT NULL DEFAULT '0',
  `calculated_revision` bigint unsigned DEFAULT NULL,
  `date_from` date DEFAULT NULL,
  `date_to` date DEFAULT NULL,
  `calculated_at` datetime(6) DEFAULT NULL,
  `as_of` datetime(6) DEFAULT NULL,
  `next_exam_start_at` datetime(6) DEFAULT NULL,
  `active_job_id` int DEFAULT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  CONSTRAINT `ck_rating_source_singleton` CHECK ((`id` = 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- schema_migrations
CREATE TABLE `schema_migrations` (
  `version` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `checksum` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `applied_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- schools
CREATE TABLE `schools` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'Название школы',
  `short_name` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Короткое название для таблиц',
  `notes` text COLLATE utf8mb4_unicode_ci COMMENT 'Комментарий администратора',
  `is_active` tinyint(1) NOT NULL DEFAULT '1' COMMENT '0 — деактивирована',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_schools_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- student_card_progress
CREATE TABLE `student_card_progress` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `section_kind` enum('manual','test','exam') COLLATE utf8mb4_unicode_ci NOT NULL,
  `section_ref_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `card_ref` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content_fingerprint` char(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `learned_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_card_ref` (`student_id`,`card_ref`),
  KEY `idx_scp_student_section` (`student_id`,`section_kind`,`section_ref_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- student_credentials
CREATE TABLE `student_credentials` (
  `student_id` int NOT NULL,
  `login` varchar(255) NOT NULL,
  `password` varchar(255) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`student_id`),
  CONSTRAINT `fk_student_credentials_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- student_progress
CREATE TABLE `student_progress` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `theme_id` int NOT NULL,
  `question_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `theme_id` (`theme_id`),
  KEY `question_id` (`question_id`),
  CONSTRAINT `student_progress_ibfk_1` FOREIGN KEY (`theme_id`) REFERENCES `card_themes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `student_progress_ibfk_2` FOREIGN KEY (`question_id`) REFERENCES `cards` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- student_section_study_settings
CREATE TABLE `student_section_study_settings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `section_kind` enum('manual','test','exam') COLLATE utf8mb4_unicode_ci NOT NULL,
  `section_ref_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `batch_size` int NOT NULL DEFAULT '10',
  `last_batch_index` int DEFAULT NULL,
  `study_mode` enum('all','unlearned','learned','stale') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'unlearned',
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `last_part_id` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_section_settings` (`student_id`,`section_kind`,`section_ref_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- students
CREATE TABLE `students` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(50) NOT NULL,
  `group_id` int DEFAULT NULL,
  `school_id` int DEFAULT NULL,
  `class` int NOT NULL,
  `tg_name` varchar(30) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_students_group_id` (`group_id`),
  KEY `idx_students_full_name` (`full_name`),
  KEY `idx_students_school_id` (`school_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- supervisors
CREATE TABLE `supervisors` (
  `id` int NOT NULL AUTO_INCREMENT,
  `full_name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- telegram_bot_chats
CREATE TABLE `telegram_bot_chats` (
  `chat_id` bigint NOT NULL,
  `student_id` int DEFAULT NULL,
  `telegram_username` varchar(255) DEFAULT NULL,
  `first_seen_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `last_seen_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `last_credentials_sent_at` timestamp NULL DEFAULT NULL,
  `messages_count` int NOT NULL DEFAULT '0',
  `last_error` text,
  PRIMARY KEY (`chat_id`),
  KEY `idx_telegram_bot_chats_student` (`student_id`),
  CONSTRAINT `fk_telegram_bot_chats_student` FOREIGN KEY (`student_id`) REFERENCES `students` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- telegram_bot_settings
CREATE TABLE `telegram_bot_settings` (
  `id` tinyint NOT NULL,
  `bot_token` varchar(255) DEFAULT NULL,
  `autostart` tinyint(1) NOT NULL DEFAULT '0',
  `welcome_text` text,
  `not_found_text` text,
  `credentials_text` text,
  `button_label` varchar(255) NOT NULL DEFAULT 'Узнать логин и пароль',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- test_sessions
CREATE TABLE `test_sessions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `test_id` int NOT NULL,
  `rate` float NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- tests_out
CREATE TABLE `tests_out` (
  `id` int NOT NULL AUTO_INCREMENT,
  `direction_id` int NOT NULL,
  `name` varchar(100) NOT NULL,
  `date` date NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- user_import_job_results
CREATE TABLE `user_import_job_results` (
  `job_id` int NOT NULL,
  `rows_data` json NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`job_id`),
  CONSTRAINT `fk_user_import_job_results_job` FOREIGN KEY (`job_id`) REFERENCES `user_import_jobs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- user_import_jobs
CREATE TABLE `user_import_jobs` (
  `id` int NOT NULL AUTO_INCREMENT,
  `session_id` int NOT NULL,
  `import_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'users',
  `status` enum('queued','running','rolling_back','completed','failed') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'queued',
  `created_by` int DEFAULT NULL,
  `created_by_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `total_rows` int NOT NULL DEFAULT '0',
  `processed_count` int NOT NULL DEFAULT '0',
  `successful` int NOT NULL DEFAULT '0',
  `skipped` int NOT NULL DEFAULT '0',
  `failed` int NOT NULL DEFAULT '0',
  `message` text COLLATE utf8mb4_unicode_ci,
  `errors` json DEFAULT NULL,
  `entities_created` json DEFAULT NULL,
  `summary` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_user_import_jobs_status` (`status`),
  KEY `idx_user_import_jobs_created_at` (`created_at`),
  KEY `idx_user_import_jobs_session` (`session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- user_import_sessions
CREATE TABLE `user_import_sessions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `import_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'users',
  `source_filename` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `preview_payload` json NOT NULL,
  `created_by` int DEFAULT NULL,
  `created_by_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_user_import_sessions_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- zap_dates
CREATE TABLE `zap_dates` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `zap_id` int NOT NULL COMMENT 'FK zaps.id (signed INT на prod)',
  `date` date NOT NULL,
  `status` enum('pending','linked','no_class_day','failed','cancelled') NOT NULL DEFAULT 'pending',
  `class_day_id` int unsigned DEFAULT NULL,
  `error_code` varchar(50) DEFAULT NULL,
  `error_message` varchar(255) DEFAULT NULL,
  `linked_at` datetime DEFAULT NULL,
  `last_retry_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_zap_date` (`zap_id`,`date`),
  KEY `fk_zd_class_day` (`class_day_id`),
  KEY `idx_zap_dates_zap` (`zap_id`),
  KEY `idx_zap_dates_status` (`status`),
  CONSTRAINT `fk_zd_class_day` FOREIGN KEY (`class_day_id`) REFERENCES `class_days` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_zd_zap` FOREIGN KEY (`zap_id`) REFERENCES `zaps` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- zap_img
CREATE TABLE `zap_img` (
  `id` int NOT NULL AUTO_INCREMENT,
  `zap_id` int NOT NULL,
  `img` longblob NOT NULL,
  `type` varchar(20) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- zaps
CREATE TABLE `zaps` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `text` text NOT NULL,
  `status` enum('set','apr','dec') NOT NULL,
  `answer` text NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
