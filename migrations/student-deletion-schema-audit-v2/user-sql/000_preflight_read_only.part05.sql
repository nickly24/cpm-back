-- COMPLETE LITERAL SQL: 000_preflight_read_only.sql, part 5/5.
-- Read README before mutating phases. Run parts in numerical order.

DELIMITER $$
SELECT 'fk_orphan:fk_classic_definition_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_definition_versions` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_threshold_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_grade_thresholds` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_part_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_parts` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_presented_attempt' AS check_name, (SELECT COUNT(*) FROM `classic_exam_presented_questions` c LEFT JOIN `classic_exam_attempts` p ON p.`id`=c.`attempt_id` WHERE c.`attempt_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_presented_definition' AS check_name, (SELECT COUNT(*) FROM `classic_exam_presented_questions` c LEFT JOIN `classic_exam_definition_questions` p ON p.`id`=c.`definition_question_id` WHERE c.`definition_question_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_presented_replacer' AS check_name, (SELECT COUNT(*) FROM `classic_exam_presented_questions` c LEFT JOIN `examinators` p ON p.`id`=c.`replaced_by_examinator_id` WHERE c.`replaced_by_examinator_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_question_import_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_question_import_sessions` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_question_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_questions` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_question_part' AS check_name, (SELECT COUNT(*) FROM `classic_exam_questions` c LEFT JOIN `classic_exam_parts` p ON p.`id`=c.`part_id` WHERE c.`part_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_settings_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_settings` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_settings_part' AS check_name, (SELECT COUNT(*) FROM `classic_exam_settings` c LEFT JOIN `classic_exam_parts` p ON p.`id`=c.`tie_breaker_part_id` WHERE c.`tie_breaker_part_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_privilege_exam' AS check_name, (SELECT COUNT(*) FROM `classic_exam_student_privileges` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_privilege_student' AS check_name, (SELECT COUNT(*) FROM `classic_exam_student_privileges` c LEFT JOIN `students` p ON p.`id`=c.`student_id` WHERE c.`student_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_round_attempt' AS check_name, (SELECT COUNT(*) FROM `classic_exam_vote_rounds` c LEFT JOIN `classic_exam_attempts` p ON p.`id`=c.`attempt_id` WHERE c.`attempt_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_round_presented' AS check_name, (SELECT COUNT(*) FROM `classic_exam_vote_rounds` c LEFT JOIN `classic_exam_presented_questions` p ON p.`id`=c.`presented_question_id` WHERE c.`presented_question_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_vote_examinator' AS check_name, (SELECT COUNT(*) FROM `classic_exam_votes` c LEFT JOIN `examinators` p ON p.`id`=c.`examinator_id` WHERE c.`examinator_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_classic_vote_round' AS check_name, (SELECT COUNT(*) FROM `classic_exam_votes` c LEFT JOIN `classic_exam_vote_rounds` p ON p.`id`=c.`round_id` WHERE c.`round_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_exam_sessions_exam' AS check_name, (SELECT COUNT(*) FROM `exam_sessions` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_exam_sessions_student' AS check_name, (SELECT COUNT(*) FROM `exam_sessions` c LEFT JOIN `students` p ON p.`id`=c.`student_id` WHERE c.`student_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_exams_direction' AS check_name, (SELECT COUNT(*) FROM `exams` c LEFT JOIN `directions` p ON p.`id`=c.`direction_id` WHERE c.`direction_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_hw_followers_thread' AS check_name, (SELECT COUNT(*) FROM `homework_chat_admin_followers` c LEFT JOIN `homework_chat_threads` p ON p.`id`=c.`thread_id` WHERE c.`thread_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_hw_messages_thread' AS check_name, (SELECT COUNT(*) FROM `homework_chat_messages` c LEFT JOIN `homework_chat_threads` p ON p.`id`=c.`thread_id` WHERE c.`thread_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_hw_reads_thread' AS check_name, (SELECT COUNT(*) FROM `homework_chat_reads` c LEFT JOIN `homework_chat_threads` p ON p.`id`=c.`thread_id` WHERE c.`thread_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_hw_jobs_submission' AS check_name, (SELECT COUNT(*) FROM `homework_file_jobs` c LEFT JOIN `homework_submissions` p ON p.`id`=c.`submission_id` WHERE c.`submission_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:homework_sessions_ibfk_1' AS check_name, (SELECT COUNT(*) FROM `homework_sessions` c LEFT JOIN `homework` p ON p.`id`=c.`homework_id` WHERE c.`homework_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:homework_sessions_ibfk_2' AS check_name, (SELECT COUNT(*) FROM `homework_sessions` c LEFT JOIN `students` p ON p.`id`=c.`student_id` WHERE c.`student_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_hw_files_submission' AS check_name, (SELECT COUNT(*) FROM `homework_submission_files` c LEFT JOIN `homework_submissions` p ON p.`id`=c.`submission_id` WHERE c.`submission_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_outside_import_exam' AS check_name, (SELECT COUNT(*) FROM `outside_exam_result_import_sessions` c LEFT JOIN `exams` p ON p.`id`=c.`exam_id` WHERE c.`exam_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_rating_staging_job' AS check_name, (SELECT COUNT(*) FROM `rating_recalc_staging` c LEFT JOIN `rating_recalc_jobs` p ON p.`id`=c.`job_id` WHERE c.`job_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_student_credentials_student' AS check_name, (SELECT COUNT(*) FROM `student_credentials` c LEFT JOIN `students` p ON p.`id`=c.`student_id` WHERE c.`student_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:student_progress_ibfk_1' AS check_name, (SELECT COUNT(*) FROM `student_progress` c LEFT JOIN `card_themes` p ON p.`id`=c.`theme_id` WHERE c.`theme_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:student_progress_ibfk_2' AS check_name, (SELECT COUNT(*) FROM `student_progress` c LEFT JOIN `cards` p ON p.`id`=c.`question_id` WHERE c.`question_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_telegram_bot_chats_student' AS check_name, (SELECT COUNT(*) FROM `telegram_bot_chats` c LEFT JOIN `students` p ON p.`id`=c.`student_id` WHERE c.`student_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_user_import_job_results_job' AS check_name, (SELECT COUNT(*) FROM `user_import_job_results` c LEFT JOIN `user_import_jobs` p ON p.`id`=c.`job_id` WHERE c.`job_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_zd_class_day' AS check_name, (SELECT COUNT(*) FROM `zap_dates` c LEFT JOIN `class_days` p ON p.`id`=c.`class_day_id` WHERE c.`class_day_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'fk_orphan:fk_zd_zap' AS check_name, (SELECT COUNT(*) FROM `zap_dates` c LEFT JOIN `zaps` p ON p.`id`=c.`zap_id` WHERE c.`zap_id` IS NOT NULL AND p.`id` IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:Allratings' AS check_name, (SELECT COUNT(*) FROM `Allratings` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:attendance' AS check_name, (SELECT COUNT(*) FROM `attendance` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:class_day_attendance' AS check_name, (SELECT COUNT(*) FROM `class_day_attendance` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:classic_exam_assignments' AS check_name, (SELECT COUNT(*) FROM `classic_exam_assignments` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:classic_exam_attempts' AS check_name, (SELECT COUNT(*) FROM `classic_exam_attempts` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:classic_exam_student_privileges' AS check_name, (SELECT COUNT(*) FROM `classic_exam_student_privileges` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:exam_sessions' AS check_name, (SELECT COUNT(*) FROM `exam_sessions` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:homework_chat_threads' AS check_name, (SELECT COUNT(*) FROM `homework_chat_threads` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:homework_file_jobs' AS check_name, (SELECT COUNT(*) FROM `homework_file_jobs` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:homework_sessions' AS check_name, (SELECT COUNT(*) FROM `homework_sessions` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:homework_submissions' AS check_name, (SELECT COUNT(*) FROM `homework_submissions` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:notifications' AS check_name, (SELECT COUNT(*) FROM `notifications` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:rating_recalc_staging' AS check_name, (SELECT COUNT(*) FROM `rating_recalc_staging` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:student_card_progress' AS check_name, (SELECT COUNT(*) FROM `student_card_progress` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:student_credentials' AS check_name, (SELECT COUNT(*) FROM `student_credentials` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:student_progress' AS check_name, (SELECT COUNT(*) FROM `student_progress` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:student_section_study_settings' AS check_name, (SELECT COUNT(*) FROM `student_section_study_settings` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:telegram_bot_chats' AS check_name, (SELECT COUNT(*) FROM `telegram_bot_chats` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:test_sessions' AS check_name, (SELECT COUNT(*) FROM `test_sessions` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'logical_student_orphan:zaps' AS check_name, (SELECT COUNT(*) FROM `zaps` t LEFT JOIN students s ON s.id=t.student_id WHERE t.student_id IS NOT NULL AND s.id IS NULL) AS mismatch_count$$

SELECT 'role_student_orphan:auth_users' AS check_name, (SELECT COUNT(*) FROM `auth_users` t LEFT JOIN students s ON s.id=t.`ref_id` WHERE t.`role`='student' AND (t.`ref_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'role_student_orphan:push_subscriptions' AS check_name, (SELECT COUNT(*) FROM `push_subscriptions` t LEFT JOIN students s ON s.id=t.`user_id` WHERE t.`user_role`='student' AND (t.`user_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'role_student_orphan:notifications' AS check_name, (SELECT COUNT(*) FROM `notifications` t LEFT JOIN students s ON s.id=t.`recipient_id` WHERE t.`recipient_role`='student' AND (t.`recipient_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'role_student_orphan:homework_chat_messages' AS check_name, (SELECT COUNT(*) FROM `homework_chat_messages` t LEFT JOIN students s ON s.id=t.`sender_id` WHERE t.`sender_role`='student' AND (t.`sender_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'role_student_orphan:homework_chat_reads' AS check_name, (SELECT COUNT(*) FROM `homework_chat_reads` t LEFT JOIN students s ON s.id=t.`reader_id` WHERE t.`reader_role`='student' AND (t.`reader_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'role_student_orphan:homework_realtime_presence' AS check_name, (SELECT COUNT(*) FROM `homework_realtime_presence` t LEFT JOIN students s ON s.id=t.`actor_id` WHERE t.`actor_role`='student' AND (t.`actor_id` IS NULL OR s.id IS NULL)) AS mismatch_count$$

SELECT 'logical_thread_orphan:homework_realtime_presence' AS check_name, (SELECT COUNT(*) FROM `homework_realtime_presence` t LEFT JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.thread_id IS NOT NULL AND h.id IS NULL) AS mismatch_count$$

SELECT 'logical_thread_orphan:homework_realtime_outbox' AS check_name, (SELECT COUNT(*) FROM `homework_realtime_outbox` t LEFT JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.thread_id IS NOT NULL AND h.id IS NULL) AS mismatch_count$$

SELECT 'logical_thread_orphan:notifications' AS check_name, (SELECT COUNT(*) FROM `notifications` t LEFT JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.thread_id IS NOT NULL AND h.id IS NULL) AS mismatch_count$$

SELECT 'cross_student_thread:homework_chat_messages' AS check_name, (SELECT COUNT(*) FROM `homework_chat_messages` t JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.`sender_role`='student' AND t.`sender_id`<>h.student_id) AS mismatch_count$$

SELECT 'cross_student_thread:homework_chat_reads' AS check_name, (SELECT COUNT(*) FROM `homework_chat_reads` t JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.`reader_role`='student' AND t.`reader_id`<>h.student_id) AS mismatch_count$$

SELECT 'cross_student_thread:homework_realtime_presence' AS check_name, (SELECT COUNT(*) FROM `homework_realtime_presence` t JOIN homework_chat_threads h ON h.id=t.thread_id WHERE t.`actor_role`='student' AND t.`actor_id`<>h.student_id) AS mismatch_count$$

SELECT 'notification_owner_disagreement' AS check_name, (SELECT COUNT(*) FROM notifications n LEFT JOIN homework_chat_threads t ON t.id=n.thread_id WHERE (n.student_id IS NOT NULL AND t.student_id IS NOT NULL AND n.student_id<>t.student_id) OR (n.recipient_role='student' AND n.student_id IS NOT NULL AND n.recipient_id<>n.student_id) OR (n.recipient_role='student' AND t.student_id IS NOT NULL AND n.recipient_id<>t.student_id)) AS mismatch_count$$

SELECT 'homework_job_owner_disagreement' AS check_name, (SELECT COUNT(*) FROM homework_file_jobs j JOIN homework_submissions s ON s.id=j.submission_id WHERE j.student_id<>s.student_id OR j.homework_id<>s.homework_id) AS mismatch_count$$

SELECT 'classic_attempt_owner_disagreement' AS check_name, (SELECT COUNT(*) FROM classic_exam_attempts a JOIN classic_exam_assignments g ON g.id=a.assignment_id WHERE a.student_id<>g.student_id OR a.exam_id<>g.exam_id) AS mismatch_count$$

SELECT 'cross_schema_incoming_or_outgoing_fk' AS check_name, (SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA IS NOT NULL AND ((TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_SCHEMA<>DATABASE()) OR (REFERENCED_TABLE_SCHEMA=DATABASE() AND TABLE_SCHEMA<>DATABASE()))) AS mismatch_count$$

SELECT 'scheduled_events' AS check_name, (SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=DATABASE()) AS mismatch_count$$

SELECT 'non_ascii_historical_key:homework_submission_files' AS check_name, (SELECT COUNT(*) FROM `homework_submission_files` WHERE `object_key` IS NOT NULL AND OCTET_LENGTH(`object_key`)<>CHAR_LENGTH(`object_key`)) AS mismatch_count$$

SELECT 'non_ascii_historical_key:homework_file_jobs' AS check_name, (SELECT COUNT(*) FROM `homework_file_jobs` WHERE `staging_key` IS NOT NULL AND OCTET_LENGTH(`staging_key`)<>CHAR_LENGTH(`staging_key`)) AS mismatch_count$$

SELECT 'homework_current_file_id_owner_mismatch' AS check_name, (SELECT COUNT(*) FROM homework_submissions s LEFT JOIN homework_submission_files f ON f.id=s.current_file_id WHERE s.current_file_id IS NOT NULL AND (f.id IS NULL OR f.submission_id<>s.id)) AS mismatch_count$$

SELECT 'homework_draft_file_id_owner_mismatch' AS check_name, (SELECT COUNT(*) FROM homework_submissions s LEFT JOIN homework_submission_files f ON f.id=s.draft_file_id WHERE s.draft_file_id IS NOT NULL AND (f.id IS NULL OR f.submission_id<>s.id)) AS mismatch_count$$

SELECT 'homework_job_result_owner_mismatch' AS check_name, (SELECT COUNT(*) FROM homework_file_jobs j LEFT JOIN homework_submission_files f ON f.id=j.result_file_id WHERE j.result_file_id IS NOT NULL AND (f.id IS NULL OR f.submission_id<>j.submission_id)) AS mismatch_count$$

SELECT 'outbox_unknown_event' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox WHERE BINARY event_type NOT IN ('message.created','message.read','typing.changed','submission.changed','job.progress','notification.created')) AS mismatch_count$$

SELECT 'outbox_nonobject_payload' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox WHERE payload_json IS NOT NULL AND JSON_TYPE(payload_json)<>'OBJECT') AS mismatch_count$$

SELECT 'outbox_invalid_user_envelope' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox WHERE BINARY event_type IN ('submission.changed','job.progress','notification.created') AND NOT COALESCE(thread_id IS NULL AND JSON_TYPE(payload_json)='OBJECT' AND JSON_LENGTH(payload_json)=2 AND JSON_CONTAINS_PATH(payload_json,'all','$.room','$.data') AND JSON_TYPE(JSON_EXTRACT(payload_json,'$.data'))='OBJECT' AND JSON_TYPE(JSON_EXTRACT(payload_json,'$.room'))='STRING' AND REGEXP_LIKE(JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.room')),'^user:(student|admin|proctor|examinator|supervisor|staff_admin):[1-9][0-9]*$','c'),FALSE)) AS mismatch_count$$

SELECT 'outbox_notification_entity_orphan' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox o LEFT JOIN notifications n ON n.id=o.entity_id WHERE BINARY o.event_type='notification.created' AND n.id IS NULL) AS mismatch_count$$

SELECT 'outbox_message_entity_orphan' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox o LEFT JOIN homework_chat_messages m ON m.id=o.entity_id WHERE BINARY o.event_type='message.created' AND o.entity_id IS NOT NULL AND m.id IS NULL) AS mismatch_count$$

SELECT 'outbox_message_thread_mismatch' AS check_name, (SELECT COUNT(*) FROM homework_realtime_outbox o JOIN homework_chat_messages m ON m.id=o.entity_id WHERE BINARY o.event_type IN ('message.created','message.read') AND o.thread_id IS NOT NULL AND o.thread_id<>m.thread_id) AS mismatch_count$$
DELIMITER ;
