-- STOPPED-WRITER ACTIVATION. DDL auto-commits: keep every writer stopped on any failure.
-- Complete literal SQL, including every schema-specific per-table trigger.
-- Never run with --force. Do not enable deletion until 032 passes and all app checks pass.
DELIMITER $$
SET @cpm_gate_sql=IF(COALESCE(@cpm_deletion_maintenance_confirmed,0)=1,'SELECT 1 AS operator_acknowledged','SELECT * FROM information_schema.CPM_STOP_ALL_WRITERS_BEFORE_ACTIVATION')$$

PREPARE cpm_gate FROM @cpm_gate_sql$$

EXECUTE cpm_gate$$

DEALLOCATE PREPARE cpm_gate$$

CREATE TABLE IF NOT EXISTS student_deletion_guard_state ( id TINYINT NOT NULL PRIMARY KEY, schema_version INT NOT NULL, CONSTRAINT ck_student_deletion_guard_singleton CHECK (id=1) ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci$$

INSERT IGNORE INTO student_deletion_guard_state(id,schema_version) VALUES(1,1)$$

DROP PROCEDURE IF EXISTS cpm_assert_student_write$$

CREATE PROCEDURE cpm_assert_student_write(IN p_student_id BIGINT) BEGIN DECLARE guard_version INT DEFAULT NULL; DECLARE blocked_job CHAR(36) DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET blocked_job=NULL; SELECT schema_version INTO guard_version FROM student_deletion_guard_state WHERE id=1 LOCK IN SHARE MODE; IF guard_version IS NULL OR guard_version<>1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_guard_unavailable'; END IF; IF p_student_id IS NOT NULL THEN SELECT job_id INTO blocked_job FROM student_deletion_barriers WHERE student_id=p_student_id LOCK IN SHARE MODE; IF blocked_job IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_pending'; END IF; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_journal_id$$

CREATE PROCEDURE cpm_assert_student_journal_id(IN p_value JSON) BEGIN DECLARE normalized JSON; DECLARE scalar_text LONGTEXT; DECLARE owner_id BIGINT DEFAULT NULL; SET normalized=p_value; IF p_value IS NOT NULL AND JSON_TYPE(p_value)='STRING' THEN SET scalar_text=JSON_UNQUOTE(p_value); IF NOT REGEXP_LIKE(scalar_text,'^[0-9]+$','c') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF; SET scalar_text=TRIM(LEADING '0' FROM scalar_text); IF scalar_text='' THEN SET scalar_text='0'; END IF; SET normalized=JSON_QUOTE(scalar_text); END IF; CALL cpm_assert_student_json_id(normalized,FALSE,owner_id); CALL cpm_assert_student_write(owner_id); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_json_write$$

CREATE PROCEDURE cpm_assert_student_json_write(IN p_document JSON) BEGIN DECLARE paths JSON; DECLARE matches_json JSON; DECLARE value_json JSON; DECLARE path_index INT DEFAULT 0; DECLARE match_index INT DEFAULT 0; DECLARE item_index INT DEFAULT 0; CALL cpm_assert_student_write(NULL); SET paths=JSON_ARRAY('$**.student_id','$**.studentId','$**.studentID', '$**.existing_student_id','$**.target_student_id','$**.privilegeBaselineStudentId', '$**.student.id','$**.student_ids','$**.studentIds','$**.student_ids_created'); IF p_document IS NOT NULL THEN WHILE path_index<JSON_LENGTH(paths) DO SET matches_json=JSON_EXTRACT(p_document,JSON_UNQUOTE(JSON_EXTRACT(paths,CONCAT('$[',path_index,']')))); SET match_index=0; WHILE matches_json IS NOT NULL AND match_index<JSON_LENGTH(matches_json) DO SET value_json=JSON_EXTRACT(matches_json,CONCAT('$[',match_index,']')); IF path_index<7 THEN CALL cpm_assert_student_journal_id(value_json); ELSEIF JSON_TYPE(value_json)<>'NULL' THEN IF JSON_TYPE(value_json)<>'ARRAY' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF; SET item_index=0; WHILE item_index<JSON_LENGTH(value_json) DO CALL cpm_assert_student_journal_id(JSON_EXTRACT(value_json,CONCAT('$[',item_index,']'))); SET item_index=item_index+1; END WHILE; END IF; SET match_index=match_index+1; END WHILE; SET path_index=path_index+1; END WHILE; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_role_write$$

CREATE PROCEDURE cpm_assert_student_role_write(IN p_role VARCHAR(32), IN p_id BIGINT, IN p_owner BIGINT) BEGIN CALL cpm_assert_student_write(NULL); IF p_role IS NULL OR BINARY p_role NOT IN ('student','admin','proctor','examinator','supervisor','staff_admin') OR p_id IS NULL OR p_id<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_role_invalid'; END IF; IF BINARY p_role='student' THEN IF p_owner IS NOT NULL AND p_owner<>p_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_write(p_id); END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_thread_write$$

CREATE PROCEDURE cpm_assert_student_thread_write(IN p_thread BIGINT, OUT p_owner BIGINT) BEGIN DECLARE CONTINUE HANDLER FOR NOT FOUND SET p_owner=NULL; CALL cpm_assert_student_write(NULL); SET p_owner=NULL; SELECT student_id INTO p_owner FROM homework_chat_threads WHERE id=p_thread LOCK IN SHARE MODE; IF p_owner IS NULL OR p_owner<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(p_owner); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_message_write$$

CREATE PROCEDURE cpm_assert_student_message_write(IN p_message BIGINT, IN p_thread BIGINT, OUT p_owner BIGINT) BEGIN DECLARE message_thread BIGINT DEFAULT NULL; DECLARE sender_role VARCHAR(32) DEFAULT NULL; DECLARE sender_id BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET message_thread=NULL; CALL cpm_assert_student_write(NULL); SELECT thread_id,m.sender_role,m.sender_id INTO message_thread,sender_role,sender_id FROM homework_chat_messages m WHERE id=p_message LOCK IN SHARE MODE; IF message_thread IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF p_thread IS NOT NULL AND p_thread<>message_thread THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_thread_write(message_thread,p_owner); IF BINARY sender_role='system' AND sender_id IS NULL THEN CALL cpm_assert_student_write(NULL); ELSE CALL cpm_assert_student_role_write(sender_role,sender_id,p_owner); END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_notification_write$$

CREATE PROCEDURE cpm_assert_student_notification_write(IN p_role VARCHAR(32), IN p_recipient BIGINT, IN p_student BIGINT, IN p_thread BIGINT, OUT p_owner BIGINT) BEGIN CALL cpm_assert_student_write(NULL); SET p_owner=p_student; IF p_student IS NOT NULL AND p_student<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_invalid'; END IF; IF p_thread IS NOT NULL THEN CALL cpm_assert_student_thread_write(p_thread,p_owner); IF p_student IS NOT NULL AND p_student<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END IF; CALL cpm_assert_student_role_write(p_role,p_recipient,p_owner); IF BINARY p_role='student' THEN SET p_owner=p_recipient; END IF; CALL cpm_assert_student_write(p_owner); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_json_id$$

CREATE PROCEDURE cpm_assert_student_json_id(IN p_value JSON, IN p_allow_zero BOOLEAN, OUT p_id BIGINT) BEGIN DECLARE scalar_text VARCHAR(128) DEFAULT NULL; SET p_id=NULL; IF p_value IS NOT NULL AND JSON_TYPE(p_value)<>'NULL' THEN IF JSON_TYPE(p_value) NOT IN ('INTEGER','STRING') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF; SET scalar_text=JSON_UNQUOTE(p_value); IF NOT REGEXP_LIKE(scalar_text, '^(0|[1-9][0-9]*)$', 'c') OR CHAR_LENGTH(scalar_text)>19 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF; IF CAST(scalar_text AS DECIMAL(20,0))>9223372036854775807 OR (scalar_text='0' AND NOT p_allow_zero) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF; SET p_id=CAST(scalar_text AS SIGNED); END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_outbox_write$$

CREATE PROCEDURE cpm_assert_student_outbox_write(IN p_thread BIGINT, IN p_event VARCHAR(48), IN p_entity BIGINT, IN p_payload JSON) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; DECLARE linked_owner BIGINT DEFAULT NULL; DECLARE object_id BIGINT DEFAULT NULL; DECLARE payload_id BIGINT DEFAULT NULL; DECLARE actor_id BIGINT DEFAULT NULL; DECLARE homework_id BIGINT DEFAULT NULL; DECLARE stored_homework BIGINT DEFAULT NULL; DECLARE linked_thread BIGINT DEFAULT NULL; DECLARE room_id BIGINT DEFAULT NULL; DECLARE role_name VARCHAR(32) DEFAULT NULL; DECLARE room_role VARCHAR(32) DEFAULT NULL; DECLARE room_text VARCHAR(128) DEFAULT NULL; DECLARE job_id VARCHAR(64) DEFAULT NULL; DECLARE document JSON; DECLARE data_value JSON; DECLARE job_value JSON; DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_owner=NULL; CALL cpm_assert_student_write(NULL); IF p_payload IS NULL THEN SET document=JSON_OBJECT(); ELSE SET document=p_payload; END IF; IF JSON_TYPE(document)<>'OBJECT' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; IF p_thread IS NOT NULL THEN CALL cpm_assert_student_thread_write(p_thread,owner_id); END IF; IF BINARY p_event='message.created' THEN IF JSON_LENGTH(document)<>0 THEN IF JSON_LENGTH(document)<>1 OR NOT JSON_CONTAINS_PATH(document,'all','$.message_id') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.message_id'),FALSE,payload_id); IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; END IF; IF p_entity IS NOT NULL AND payload_id IS NOT NULL AND p_entity<>payload_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; SET object_id=COALESCE(p_entity,payload_id); IF object_id IS NULL OR object_id<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_message_write(object_id,p_thread,linked_owner); ELSEIF BINARY p_event='message.read' THEN IF p_thread IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF JSON_LENGTH(document)<>0 THEN IF JSON_LENGTH(document)<>3 OR NOT JSON_CONTAINS_PATH(document,'all','$.reader_role','$.reader_id','$.last_message_id') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.reader_id'),FALSE,actor_id); CALL cpm_assert_student_role_write(JSON_UNQUOTE(JSON_EXTRACT(document,'$.reader_role')),actor_id,owner_id); CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.last_message_id'),TRUE,payload_id); IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; END IF; IF p_entity IS NOT NULL AND payload_id IS NOT NULL AND p_entity<>payload_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; SET object_id=COALESCE(p_entity,payload_id); IF object_id IS NULL OR object_id<0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF object_id<>0 THEN CALL cpm_assert_student_message_write(object_id,p_thread,linked_owner); END IF; ELSEIF BINARY p_event='typing.changed' THEN IF p_thread IS NULL OR p_entity IS NOT NULL OR JSON_LENGTH(document)<>4 OR NOT JSON_CONTAINS_PATH(document,'all','$.actor_role','$.actor_id','$.active','$.expires_in_ms') OR JSON_TYPE(JSON_EXTRACT(document,'$.active'))<>'BOOLEAN' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.actor_id'),FALSE,actor_id); CALL cpm_assert_student_role_write(JSON_UNQUOTE(JSON_EXTRACT(document,'$.actor_role')),actor_id,owner_id); CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.expires_in_ms'),FALSE,payload_id); IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; ELSEIF BINARY p_event IN ('submission.changed','job.progress','notification.created') THEN IF p_thread IS NOT NULL OR JSON_LENGTH(document)<>2 OR NOT JSON_CONTAINS_PATH(document,'all','$.room','$.data') OR JSON_TYPE(JSON_EXTRACT(document,'$.room'))<>'STRING' OR JSON_TYPE(JSON_EXTRACT(document,'$.data'))<>'OBJECT' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; SET room_text=JSON_UNQUOTE(JSON_EXTRACT(document,'$.room')); IF NOT REGEXP_LIKE(room_text,'^user:(student|admin|proctor|examinator|supervisor|staff_admin):[1-9][0-9]*$','c') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_room_invalid'; END IF; SET room_role=SUBSTRING_INDEX(SUBSTRING_INDEX(room_text,':',2),':',-1); CALL cpm_assert_student_json_id(JSON_QUOTE(SUBSTRING_INDEX(room_text,':',-1)),FALSE,room_id); SET data_value=JSON_EXTRACT(document,'$.data'); IF BINARY p_event='submission.changed' THEN IF BINARY room_role<>'student' OR JSON_LENGTH(data_value)<>3 OR NOT JSON_CONTAINS_PATH(data_value,'all','$.homework_id','$.student_id','$.submission_id') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.student_id'),FALSE,owner_id); CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.homework_id'),FALSE,homework_id); CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.submission_id'),FALSE,payload_id); IF owner_id IS NULL OR homework_id IS NULL OR NOT (p_entity<=>payload_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; IF payload_id IS NOT NULL THEN SELECT student_id,s.homework_id INTO linked_owner,stored_homework FROM homework_submissions s WHERE id=payload_id LOCK IN SHARE MODE; IF linked_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF owner_id<>linked_owner OR homework_id<>stored_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END IF; ELSEIF BINARY p_event='job.progress' THEN IF BINARY room_role<>'student' OR p_entity IS NOT NULL OR JSON_LENGTH(data_value)<>1 OR NOT JSON_CONTAINS_PATH(data_value,'all','$.job') OR JSON_TYPE(JSON_EXTRACT(data_value,'$.job'))<>'OBJECT' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; SET job_value=JSON_EXTRACT(data_value,'$.job'); IF JSON_LENGTH(job_value)<>6 OR NOT JSON_CONTAINS_PATH(job_value,'all','$.id','$.homework_id','$.status','$.stage','$.progress','$.error_code') OR JSON_TYPE(JSON_EXTRACT(job_value,'$.id'))<>'STRING' OR JSON_TYPE(JSON_EXTRACT(job_value,'$.status'))<>'STRING' OR JSON_TYPE(JSON_EXTRACT(job_value,'$.stage'))<>'STRING' OR JSON_TYPE(JSON_EXTRACT(job_value,'$.progress'))<>'INTEGER' OR JSON_TYPE(JSON_EXTRACT(job_value,'$.error_code')) NOT IN ('NULL','STRING') OR JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.status'))='' OR JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.stage'))='' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(job_value,'$.progress'),TRUE,payload_id); IF payload_id IS NULL OR payload_id>100 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; SET job_id=JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.id')); IF NOT REGEXP_LIKE(job_id,'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$','c') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(job_value,'$.homework_id'),FALSE,homework_id); SELECT j.student_id,j.homework_id,s.student_id INTO linked_owner,stored_homework,owner_id FROM homework_file_jobs j JOIN homework_submissions s ON s.id=j.submission_id WHERE BINARY j.id=BINARY job_id LOCK IN SHARE MODE; IF linked_owner IS NULL OR owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF homework_id IS NULL OR homework_id<>stored_homework OR owner_id<>linked_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; ELSE IF JSON_LENGTH(data_value)<>1 OR NOT JSON_CONTAINS_PATH(data_value,'all','$.entity_id') THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF; CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.entity_id'),FALSE,payload_id); IF payload_id IS NULL OR NOT (p_entity<=>payload_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; SET role_name=NULL; SELECT recipient_role,recipient_id,student_id,thread_id INTO role_name,actor_id,linked_owner,linked_thread FROM notifications WHERE id=payload_id LOCK IN SHARE MODE; IF role_name IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF BINARY room_role<>BINARY role_name OR room_id<>actor_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_notification_write(role_name,actor_id,linked_owner,linked_thread,owner_id); END IF; CALL cpm_assert_student_role_write(room_role,room_id,owner_id); CALL cpm_assert_student_write(owner_id); ELSE SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_event_unknown'; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_object_write$$

CREATE PROCEDURE cpm_assert_student_object_write(IN p_key VARCHAR(512), IN p_owner BIGINT, IN p_submission BIGINT) BEGIN DECLARE hit_key VARCHAR(512) DEFAULT NULL; DECLARE referenced_owner BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET hit_key=NULL; CALL cpm_assert_student_write(p_owner); IF p_key IS NOT NULL THEN IF p_key='' OR p_owner IS NULL OR p_owner<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_invalid'; END IF; SET hit_key=NULL; SELECT o.object_key,o.student_id INTO hit_key,referenced_owner FROM homework_object_ownership o WHERE BINARY o.object_key=BINARY p_key AND (o.student_id<>p_owner OR (o.submission_id IS NOT NULL AND p_submission IS NOT NULL AND o.submission_id<>p_submission)) LIMIT 1 LOCK IN SHARE MODE; IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF; SET hit_key=NULL; SELECT f.object_key,s.student_id INTO hit_key,referenced_owner FROM homework_submission_files f LEFT JOIN homework_submissions s ON s.id=f.submission_id WHERE BINARY f.object_key=BINARY p_key AND (s.id IS NULL OR s.student_id<>p_owner OR (p_submission IS NOT NULL AND f.submission_id<>p_submission)) LIMIT 1 LOCK IN SHARE MODE; IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF; SET hit_key=NULL; SELECT j.id,j.student_id INTO hit_key,referenced_owner FROM homework_file_jobs j LEFT JOIN homework_submissions s ON s.id=j.submission_id WHERE (BINARY j.staging_key=BINARY p_key OR BINARY j.processed_output_key=BINARY p_key) AND (s.id IS NULL OR j.student_id<>p_owner OR s.student_id<>p_owner OR j.homework_id<>s.homework_id OR (p_submission IS NOT NULL AND j.submission_id<>p_submission)) LIMIT 1 LOCK IN SHARE MODE; IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF; SET hit_key=NULL; SELECT o.object_key,p.student_id INTO hit_key,referenced_owner FROM homework_student_purge_objects o LEFT JOIN homework_student_purges p ON BINARY p.job_id=BINARY o.job_id WHERE BINARY o.object_key=BINARY p_key AND (p.job_id IS NULL OR p.student_id<>p_owner) LIMIT 1 LOCK IN SHARE MODE; IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_file_pointer$$

CREATE PROCEDURE cpm_assert_student_file_pointer(IN p_file BIGINT, IN p_submission BIGINT, IN p_owner BIGINT) BEGIN DECLARE file_submission BIGINT DEFAULT NULL; DECLARE file_key VARCHAR(512) DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET file_submission=NULL; CALL cpm_assert_student_write(p_owner); IF p_file IS NOT NULL THEN SELECT submission_id,object_key INTO file_submission,file_key FROM homework_submission_files WHERE id=p_file LOCK IN SHARE MODE; IF file_submission IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF p_submission IS NULL OR p_submission<>file_submission THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_file_submission_conflict'; END IF; CALL cpm_assert_student_object_write(file_key,p_owner,p_submission); END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_submission_write$$

CREATE PROCEDURE cpm_assert_student_submission_write(IN p_submission BIGINT, IN p_owner BIGINT, IN p_draft BIGINT, IN p_current BIGINT) BEGIN CALL cpm_assert_student_write(p_owner); CALL cpm_assert_student_file_pointer(p_draft,p_submission,p_owner); CALL cpm_assert_student_file_pointer(p_current,p_submission,p_owner); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_file_write$$

CREATE PROCEDURE cpm_assert_student_file_write(IN p_submission BIGINT, IN p_key VARCHAR(512)) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL; CALL cpm_assert_student_write(NULL); SELECT student_id INTO owner_id FROM homework_submissions WHERE id=p_submission LOCK IN SHARE MODE; IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_object_write(p_key,owner_id,p_submission); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_job_write$$

CREATE PROCEDURE cpm_assert_student_job_write(IN p_job VARCHAR(36), IN p_owner BIGINT, IN p_submission BIGINT, IN p_homework BIGINT, IN p_result BIGINT, IN p_staging VARCHAR(512), IN p_processed VARCHAR(512)) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; DECLARE homework_id BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL; CALL cpm_assert_student_write(p_owner); SELECT s.student_id,s.homework_id INTO owner_id,homework_id FROM homework_submissions s WHERE s.id=p_submission LOCK IN SHARE MODE; IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); IF owner_id<>p_owner OR homework_id<>p_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_file_pointer(p_result,p_submission,p_owner); CALL cpm_assert_student_object_write(p_staging,p_owner,p_submission); CALL cpm_assert_student_object_write(p_processed,p_owner,p_submission); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_ledger_write$$

CREATE PROCEDURE cpm_assert_student_ledger_write(IN p_key VARCHAR(512), IN p_owner BIGINT, IN p_submission BIGINT, IN p_job VARCHAR(36)) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; DECLARE parent_owner BIGINT DEFAULT NULL; DECLARE job_homework BIGINT DEFAULT NULL; DECLARE parent_homework BIGINT DEFAULT NULL; DECLARE job_submission BIGINT DEFAULT NULL; DECLARE resolved_submission BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL; CALL cpm_assert_student_write(p_owner); SET resolved_submission=p_submission; IF p_submission IS NOT NULL THEN SELECT student_id INTO owner_id FROM homework_submissions WHERE id=p_submission LOCK IN SHARE MODE; IF owner_id IS NOT NULL THEN CALL cpm_assert_student_write(owner_id); IF owner_id<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END IF; END IF; IF p_job IS NOT NULL THEN SET owner_id=NULL; SELECT j.student_id,j.submission_id,s.student_id,j.homework_id,s.homework_id INTO owner_id,job_submission,parent_owner,job_homework,parent_homework FROM homework_file_jobs j LEFT JOIN homework_submissions s ON s.id=j.submission_id WHERE BINARY j.id=BINARY p_job LOCK IN SHARE MODE; IF owner_id IS NOT NULL THEN CALL cpm_assert_student_write(owner_id); IF owner_id<>p_owner OR (p_submission IS NOT NULL AND p_submission<>job_submission) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; IF parent_owner IS NOT NULL THEN CALL cpm_assert_student_write(parent_owner); IF parent_owner<>p_owner OR job_homework<>parent_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END IF; SET resolved_submission=job_submission; END IF; END IF; CALL cpm_assert_student_object_write(p_key,p_owner,resolved_submission); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_attempt_owner$$

CREATE PROCEDURE cpm_assert_student_attempt_owner(IN p_attempt BIGINT, OUT p_owner BIGINT) BEGIN DECLARE assigned_owner BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET p_owner=NULL; CALL cpm_assert_student_write(NULL); SET p_owner=NULL; SELECT a.student_id,s.student_id INTO p_owner,assigned_owner FROM classic_exam_attempts a LEFT JOIN classic_exam_assignments s ON s.id=a.assignment_id WHERE a.id=p_attempt LOCK IN SHARE MODE; IF p_owner IS NULL OR assigned_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(p_owner); CALL cpm_assert_student_write(assigned_owner); IF p_owner<>assigned_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_question_link$$

CREATE PROCEDURE cpm_assert_student_question_link(IN p_question BIGINT, IN p_attempt BIGINT) BEGIN DECLARE linked_attempt BIGINT DEFAULT NULL; DECLARE owner_id BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_attempt=NULL; CALL cpm_assert_student_write(NULL); IF p_question IS NOT NULL THEN SELECT attempt_id INTO linked_attempt FROM classic_exam_presented_questions WHERE id=p_question LOCK IN SHARE MODE; IF linked_attempt IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_attempt_owner(linked_attempt,owner_id); IF p_attempt IS NULL OR linked_attempt<>p_attempt THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_question_attempt_conflict'; END IF; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_attempt_write$$

CREATE PROCEDURE cpm_assert_student_attempt_write(IN p_attempt BIGINT, IN p_owner BIGINT, IN p_assignment BIGINT, IN p_question BIGINT) BEGIN DECLARE assigned_owner BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET assigned_owner=NULL; CALL cpm_assert_student_write(p_owner); SELECT student_id INTO assigned_owner FROM classic_exam_assignments WHERE id=p_assignment LOCK IN SHARE MODE; IF assigned_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(assigned_owner); IF assigned_owner<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_question_link(p_question,p_attempt); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_presented_write$$

CREATE PROCEDURE cpm_assert_student_presented_write(IN p_attempt BIGINT, IN p_replaces BIGINT) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attempt_owner(p_attempt,owner_id); CALL cpm_assert_student_question_link(p_replaces,p_attempt); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_round_write$$

CREATE PROCEDURE cpm_assert_student_round_write(IN p_attempt BIGINT, IN p_question BIGINT) BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attempt_owner(p_attempt,owner_id); IF p_question IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_question_link(p_question,p_attempt); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_vote_write$$

CREATE PROCEDURE cpm_assert_student_vote_write(IN p_round BIGINT) BEGIN DECLARE attempt_id BIGINT DEFAULT NULL; DECLARE question_id BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET attempt_id=NULL; CALL cpm_assert_student_write(NULL); SELECT r.attempt_id,r.presented_question_id INTO attempt_id,question_id FROM classic_exam_vote_rounds r WHERE id=p_round LOCK IN SHARE MODE; IF attempt_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_round_write(attempt_id,question_id); END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_attendance_write$$

CREATE PROCEDURE cpm_assert_student_attendance_write(IN p_owner BIGINT, IN p_zap BIGINT) BEGIN DECLARE linked_owner BIGINT DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_owner=NULL; CALL cpm_assert_student_write(p_owner); IF p_zap IS NOT NULL THEN SELECT student_id INTO linked_owner FROM zaps WHERE id=p_zap LOCK IN SHARE MODE; IF linked_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(linked_owner); IF linked_owner<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; END IF; END$$

DROP PROCEDURE IF EXISTS cpm_assert_student_command_erasure$$

CREATE PROCEDURE cpm_assert_student_command_erasure(IN p_command BIGINT, OUT p_captured BOOLEAN, OUT p_erasure BOOLEAN) BEGIN DECLARE job_status VARCHAR(16) DEFAULT NULL; DECLARE CONTINUE HANDLER FOR NOT FOUND SET job_status=NULL; CALL cpm_assert_student_write(NULL); SET p_captured=FALSE; SET p_erasure=FALSE; SELECT j.status INTO job_status FROM student_deletion_jobs j JOIN student_deletion_barriers b ON b.job_id=j.id AND b.student_id=j.student_id WHERE JSON_TYPE(JSON_EXTRACT(j.manifest_json,'$.command_ids'))='ARRAY' AND JSON_CONTAINS(JSON_EXTRACT(j.manifest_json,'$.command_ids'),CAST(p_command AS JSON)) LIMIT 1 LOCK IN SHARE MODE; IF job_status IS NOT NULL THEN SET p_captured=TRUE; SET p_erasure=(BINARY job_status IN ('queued','running','waiting','failed')); END IF; END$$

DROP TRIGGER IF EXISTS cpm_sdb_insert$$

CREATE TRIGGER cpm_sdb_insert BEFORE INSERT ON student_deletion_barriers FOR EACH ROW BEGIN DECLARE guard_version INT DEFAULT NULL; SELECT schema_version INTO guard_version FROM student_deletion_guard_state WHERE id=1 FOR UPDATE; IF guard_version IS NULL OR guard_version<>1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_guard_unavailable'; END IF; END$$

DROP TRIGGER IF EXISTS cpm_sdb_update$$

CREATE TRIGGER cpm_sdb_update BEFORE UPDATE ON student_deletion_barriers FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_barrier_immutable'$$

DROP TRIGGER IF EXISTS cpm_sdb_delete$$

CREATE TRIGGER cpm_sdb_delete BEFORE DELETE ON student_deletion_barriers FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_barrier_immutable'$$

DROP TRIGGER IF EXISTS `cpm_sd_Allratings_i`$$

CREATE TRIGGER `cpm_sd_Allratings_i` BEFORE INSERT ON `Allratings` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_Allratings_u`$$

CREATE TRIGGER `cpm_sd_Allratings_u` BEFORE UPDATE ON `Allratings` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_attendance_i`$$

CREATE TRIGGER `cpm_sd_attendance_i` BEFORE INSERT ON `attendance` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_attendance_u`$$

CREATE TRIGGER `cpm_sd_attendance_u` BEFORE UPDATE ON `attendance` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_auth_users_i`$$

CREATE TRIGGER `cpm_sd_auth_users_i` BEFORE INSERT ON `auth_users` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; IF NEW.role='student' THEN CALL cpm_assert_student_write(NEW.ref_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_auth_users_u`$$

CREATE TRIGGER `cpm_sd_auth_users_u` BEFORE UPDATE ON `auth_users` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; IF OLD.role='student' THEN CALL cpm_assert_student_write(OLD.ref_id); END IF; IF NEW.role='student' THEN CALL cpm_assert_student_write(NEW.ref_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_class_day_attendance_i`$$

CREATE TRIGGER `cpm_sd_class_day_attendance_i` BEFORE INSERT ON `class_day_attendance` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attendance_write(NEW.student_id,NEW.zap_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_class_day_attendance_u`$$

CREATE TRIGGER `cpm_sd_class_day_attendance_u` BEFORE UPDATE ON `class_day_attendance` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attendance_write(OLD.student_id,OLD.zap_id); CALL cpm_assert_student_attendance_write(NEW.student_id,NEW.zap_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_appeals_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_appeals_i` BEFORE INSERT ON `classic_exam_appeals` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_appeals_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_appeals_u` BEFORE UPDATE ON `classic_exam_appeals` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=OLD.`attempt_id` LOCK IN SHARE MODE; IF OLD.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignment_import_sessions_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignment_import_sessions_i` BEFORE INSERT ON `classic_exam_assignment_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); CALL cpm_assert_student_json_write(NEW.`commit_result`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignment_import_sessions_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignment_import_sessions_u` BEFORE UPDATE ON `classic_exam_assignment_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); CALL cpm_assert_student_json_write(NEW.`commit_result`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignment_members_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignment_members_i` BEFORE INSERT ON `classic_exam_assignment_members` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_assignments` WHERE id=NEW.`assignment_id` LOCK IN SHARE MODE; IF NEW.`assignment_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignment_members_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignment_members_u` BEFORE UPDATE ON `classic_exam_assignment_members` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_assignments` WHERE id=OLD.`assignment_id` LOCK IN SHARE MODE; IF OLD.`assignment_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_assignments` WHERE id=NEW.`assignment_id` LOCK IN SHARE MODE; IF NEW.`assignment_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignments_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignments_i` BEFORE INSERT ON `classic_exam_assignments` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_assignments_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_assignments_u` BEFORE UPDATE ON `classic_exam_assignments` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_commands_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_commands_i` BEFORE INSERT ON `classic_exam_attempt_commands` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_commands_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_commands_u` BEFORE UPDATE ON `classic_exam_attempt_commands` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=OLD.`attempt_id` LOCK IN SHARE MODE; IF OLD.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_members_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_members_i` BEFORE INSERT ON `classic_exam_attempt_members` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_members_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_members_u` BEFORE UPDATE ON `classic_exam_attempt_members` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=OLD.`attempt_id` LOCK IN SHARE MODE; IF OLD.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_part_progress_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_part_progress_i` BEFORE INSERT ON `classic_exam_attempt_part_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_part_progress_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_part_progress_u` BEFORE UPDATE ON `classic_exam_attempt_part_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=OLD.`attempt_id` LOCK IN SHARE MODE; IF OLD.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_question_usage_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_question_usage_i` BEFORE INSERT ON `classic_exam_attempt_question_usage` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempt_question_usage_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempt_question_usage_u` BEFORE UPDATE ON `classic_exam_attempt_question_usage` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=OLD.`attempt_id` LOCK IN SHARE MODE; IF OLD.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `classic_exam_attempts` WHERE id=NEW.`attempt_id` LOCK IN SHARE MODE; IF NEW.`attempt_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempts_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempts_i` BEFORE INSERT ON `classic_exam_attempts` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attempt_write(NEW.id,NEW.student_id,NEW.assignment_id,NEW.current_presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_attempts_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_attempts_u` BEFORE UPDATE ON `classic_exam_attempts` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_attempt_write(OLD.id,OLD.student_id,OLD.assignment_id,OLD.current_presented_question_id); CALL cpm_assert_student_attempt_write(NEW.id,NEW.student_id,NEW.assignment_id,NEW.current_presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_presented_questions_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_presented_questions_i` BEFORE INSERT ON `classic_exam_presented_questions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_presented_write(NEW.attempt_id,NEW.replaces_presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_presented_questions_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_presented_questions_u` BEFORE UPDATE ON `classic_exam_presented_questions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_presented_write(OLD.attempt_id,OLD.replaces_presented_question_id); CALL cpm_assert_student_presented_write(NEW.attempt_id,NEW.replaces_presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_student_privileges_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_student_privileges_i` BEFORE INSERT ON `classic_exam_student_privileges` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_student_privileges_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_student_privileges_u` BEFORE UPDATE ON `classic_exam_student_privileges` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_vote_rounds_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_vote_rounds_i` BEFORE INSERT ON `classic_exam_vote_rounds` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_round_write(NEW.attempt_id,NEW.presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_vote_rounds_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_vote_rounds_u` BEFORE UPDATE ON `classic_exam_vote_rounds` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_round_write(OLD.attempt_id,OLD.presented_question_id); CALL cpm_assert_student_round_write(NEW.attempt_id,NEW.presented_question_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_votes_i`$$

CREATE TRIGGER `cpm_sd_classic_exam_votes_i` BEFORE INSERT ON `classic_exam_votes` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_vote_write(NEW.round_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_classic_exam_votes_u`$$

CREATE TRIGGER `cpm_sd_classic_exam_votes_u` BEFORE UPDATE ON `classic_exam_votes` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_vote_write(OLD.round_id); CALL cpm_assert_student_vote_write(NEW.round_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_exam_admin_commands_i`$$

CREATE TRIGGER `cpm_sd_exam_admin_commands_i` BEFORE INSERT ON `exam_admin_commands` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.target_student_id); IF NEW.target_attempt_id IS NOT NULL THEN SET owner_id=NULL; SELECT student_id INTO owner_id FROM classic_exam_attempts WHERE id=NEW.target_attempt_id LOCK IN SHARE MODE; IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF NEW.target_student_id IS NOT NULL AND NEW.target_student_id<>owner_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_write(owner_id); END IF; CALL cpm_assert_student_json_write(NEW.`receipt`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_exam_admin_commands_u`$$

CREATE TRIGGER `cpm_sd_exam_admin_commands_u` BEFORE UPDATE ON `exam_admin_commands` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; DECLARE command_captured BOOLEAN DEFAULT FALSE; DECLARE command_erasure BOOLEAN DEFAULT FALSE; CALL cpm_assert_student_command_erasure(OLD.id,command_captured,command_erasure); IF command_erasure AND NEW.target_student_id IS NULL AND NEW.target_attempt_id IS NULL AND NEW.receipt IS NULL AND NEW.tombstone=1 AND (BINARY NEW.`id`<=>BINARY OLD.`id`) AND (BINARY NEW.`actor_role`<=>BINARY OLD.`actor_role`) AND (BINARY NEW.`actor_id`<=>BINARY OLD.`actor_id`) AND (BINARY NEW.`idempotency_key`<=>BINARY OLD.`idempotency_key`) AND (BINARY NEW.`command_type`<=>BINARY OLD.`command_type`) AND (BINARY NEW.`target_exam_id`<=>BINARY OLD.`target_exam_id`) AND (BINARY NEW.`request_hash`<=>BINARY OLD.`request_hash`) AND (BINARY NEW.`created_at`<=>BINARY OLD.`created_at`) AND (BINARY NEW.`expires_at`<=>BINARY OLD.`expires_at`) THEN CALL cpm_assert_student_write(NULL); ELSE IF command_captured THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_command_tombstone_immutable'; END IF; CALL cpm_assert_student_write(OLD.target_student_id); IF OLD.target_attempt_id IS NOT NULL THEN SET owner_id=NULL; SELECT student_id INTO owner_id FROM classic_exam_attempts WHERE id=OLD.target_attempt_id LOCK IN SHARE MODE; IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF OLD.target_student_id IS NOT NULL AND OLD.target_student_id<>owner_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_write(owner_id); END IF; CALL cpm_assert_student_write(NEW.target_student_id); IF NEW.target_attempt_id IS NOT NULL THEN SET owner_id=NULL; SELECT student_id INTO owner_id FROM classic_exam_attempts WHERE id=NEW.target_attempt_id LOCK IN SHARE MODE; IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; IF NEW.target_student_id IS NOT NULL AND NEW.target_student_id<>owner_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF; CALL cpm_assert_student_write(owner_id); END IF; CALL cpm_assert_student_json_write(NEW.`receipt`); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_exam_admin_commands_d`$$

CREATE TRIGGER `cpm_sd_exam_admin_commands_d` BEFORE DELETE ON `exam_admin_commands` FOR EACH ROW BEGIN DECLARE command_captured BOOLEAN DEFAULT FALSE; DECLARE command_erasure BOOLEAN DEFAULT FALSE; CALL cpm_assert_student_command_erasure(OLD.id,command_captured,command_erasure); IF command_captured THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_command_tombstone_immutable'; END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_exam_sessions_i`$$

CREATE TRIGGER `cpm_sd_exam_sessions_i` BEFORE INSERT ON `exam_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_exam_sessions_u`$$

CREATE TRIGGER `cpm_sd_exam_sessions_u` BEFORE UPDATE ON `exam_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_admin_followers_i`$$

CREATE TRIGGER `cpm_sd_homework_chat_admin_followers_i` BEFORE INSERT ON `homework_chat_admin_followers` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `homework_chat_threads` WHERE id=NEW.`thread_id` LOCK IN SHARE MODE; IF NEW.`thread_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_admin_followers_u`$$

CREATE TRIGGER `cpm_sd_homework_chat_admin_followers_u` BEFORE UPDATE ON `homework_chat_admin_followers` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `homework_chat_threads` WHERE id=OLD.`thread_id` LOCK IN SHARE MODE; IF OLD.`thread_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `homework_chat_threads` WHERE id=NEW.`thread_id` LOCK IN SHARE MODE; IF NEW.`thread_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_messages_i`$$

CREATE TRIGGER `cpm_sd_homework_chat_messages_i` BEFORE INSERT ON `homework_chat_messages` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); IF BINARY NEW.sender_role='system' AND NEW.sender_id IS NULL THEN CALL cpm_assert_student_write(NULL); ELSE CALL cpm_assert_student_role_write(NEW.sender_role,NEW.sender_id,owner_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_messages_u`$$

CREATE TRIGGER `cpm_sd_homework_chat_messages_u` BEFORE UPDATE ON `homework_chat_messages` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(OLD.thread_id,owner_id); IF BINARY OLD.sender_role='system' AND OLD.sender_id IS NULL THEN CALL cpm_assert_student_write(NULL); ELSE CALL cpm_assert_student_role_write(OLD.sender_role,OLD.sender_id,owner_id); END IF; CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); IF BINARY NEW.sender_role='system' AND NEW.sender_id IS NULL THEN CALL cpm_assert_student_write(NULL); ELSE CALL cpm_assert_student_role_write(NEW.sender_role,NEW.sender_id,owner_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_reads_i`$$

CREATE TRIGGER `cpm_sd_homework_chat_reads_i` BEFORE INSERT ON `homework_chat_reads` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); CALL cpm_assert_student_role_write(NEW.reader_role,NEW.reader_id,owner_id); IF NEW.last_message_id<>0 THEN CALL cpm_assert_student_message_write(NEW.last_message_id,NEW.thread_id,owner_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_reads_u`$$

CREATE TRIGGER `cpm_sd_homework_chat_reads_u` BEFORE UPDATE ON `homework_chat_reads` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(OLD.thread_id,owner_id); CALL cpm_assert_student_role_write(OLD.reader_role,OLD.reader_id,owner_id); IF OLD.last_message_id<>0 THEN CALL cpm_assert_student_message_write(OLD.last_message_id,OLD.thread_id,owner_id); END IF; CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); CALL cpm_assert_student_role_write(NEW.reader_role,NEW.reader_id,owner_id); IF NEW.last_message_id<>0 THEN CALL cpm_assert_student_message_write(NEW.last_message_id,NEW.thread_id,owner_id); END IF; END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_threads_i`$$

CREATE TRIGGER `cpm_sd_homework_chat_threads_i` BEFORE INSERT ON `homework_chat_threads` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_chat_threads_u`$$

CREATE TRIGGER `cpm_sd_homework_chat_threads_u` BEFORE UPDATE ON `homework_chat_threads` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_file_jobs_i`$$

CREATE TRIGGER `cpm_sd_homework_file_jobs_i` BEFORE INSERT ON `homework_file_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_job_write(NEW.id,NEW.student_id,NEW.submission_id,NEW.homework_id,NEW.result_file_id,NEW.staging_key,NEW.processed_output_key); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_file_jobs_u`$$

CREATE TRIGGER `cpm_sd_homework_file_jobs_u` BEFORE UPDATE ON `homework_file_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_job_write(OLD.id,OLD.student_id,OLD.submission_id,OLD.homework_id,OLD.result_file_id,OLD.staging_key,OLD.processed_output_key); CALL cpm_assert_student_job_write(NEW.id,NEW.student_id,NEW.submission_id,NEW.homework_id,NEW.result_file_id,NEW.staging_key,NEW.processed_output_key); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_object_ownership_i`$$

CREATE TRIGGER `cpm_sd_homework_object_ownership_i` BEFORE INSERT ON `homework_object_ownership` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_ledger_write(NEW.object_key,NEW.student_id,NEW.submission_id,NEW.upload_job_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_object_ownership_u`$$

CREATE TRIGGER `cpm_sd_homework_object_ownership_u` BEFORE UPDATE ON `homework_object_ownership` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_ledger_write(OLD.object_key,OLD.student_id,OLD.submission_id,OLD.upload_job_id); CALL cpm_assert_student_ledger_write(NEW.object_key,NEW.student_id,NEW.submission_id,NEW.upload_job_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_realtime_outbox_i`$$

CREATE TRIGGER `cpm_sd_homework_realtime_outbox_i` BEFORE INSERT ON `homework_realtime_outbox` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_outbox_write(NEW.thread_id,NEW.event_type,NEW.entity_id,NEW.payload_json); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_realtime_outbox_u`$$

CREATE TRIGGER `cpm_sd_homework_realtime_outbox_u` BEFORE UPDATE ON `homework_realtime_outbox` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_outbox_write(OLD.thread_id,OLD.event_type,OLD.entity_id,OLD.payload_json); CALL cpm_assert_student_outbox_write(NEW.thread_id,NEW.event_type,NEW.entity_id,NEW.payload_json); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_realtime_presence_i`$$

CREATE TRIGGER `cpm_sd_homework_realtime_presence_i` BEFORE INSERT ON `homework_realtime_presence` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); CALL cpm_assert_student_role_write(NEW.actor_role,NEW.actor_id,owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_realtime_presence_u`$$

CREATE TRIGGER `cpm_sd_homework_realtime_presence_u` BEFORE UPDATE ON `homework_realtime_presence` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_thread_write(OLD.thread_id,owner_id); CALL cpm_assert_student_role_write(OLD.actor_role,OLD.actor_id,owner_id); CALL cpm_assert_student_thread_write(NEW.thread_id,owner_id); CALL cpm_assert_student_role_write(NEW.actor_role,NEW.actor_id,owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_sessions_i`$$

CREATE TRIGGER `cpm_sd_homework_sessions_i` BEFORE INSERT ON `homework_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_sessions_u`$$

CREATE TRIGGER `cpm_sd_homework_sessions_u` BEFORE UPDATE ON `homework_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_submission_files_i`$$

CREATE TRIGGER `cpm_sd_homework_submission_files_i` BEFORE INSERT ON `homework_submission_files` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_file_write(NEW.submission_id,NEW.object_key); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_submission_files_u`$$

CREATE TRIGGER `cpm_sd_homework_submission_files_u` BEFORE UPDATE ON `homework_submission_files` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_file_write(OLD.submission_id,OLD.object_key); CALL cpm_assert_student_file_write(NEW.submission_id,NEW.object_key); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_submissions_i`$$

CREATE TRIGGER `cpm_sd_homework_submissions_i` BEFORE INSERT ON `homework_submissions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_submission_write(NEW.id,NEW.student_id,NEW.draft_file_id,NEW.current_file_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_homework_submissions_u`$$

CREATE TRIGGER `cpm_sd_homework_submissions_u` BEFORE UPDATE ON `homework_submissions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_submission_write(OLD.id,OLD.student_id,OLD.draft_file_id,OLD.current_file_id); CALL cpm_assert_student_submission_write(NEW.id,NEW.student_id,NEW.draft_file_id,NEW.current_file_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_notifications_i`$$

CREATE TRIGGER `cpm_sd_notifications_i` BEFORE INSERT ON `notifications` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_notification_write(NEW.recipient_role,NEW.recipient_id,NEW.student_id,NEW.thread_id,owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_notifications_u`$$

CREATE TRIGGER `cpm_sd_notifications_u` BEFORE UPDATE ON `notifications` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_notification_write(OLD.recipient_role,OLD.recipient_id,OLD.student_id,OLD.thread_id,owner_id); CALL cpm_assert_student_notification_write(NEW.recipient_role,NEW.recipient_id,NEW.student_id,NEW.thread_id,owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_outside_exam_result_import_sessions_i`$$

CREATE TRIGGER `cpm_sd_outside_exam_result_import_sessions_i` BEFORE INSERT ON `outside_exam_result_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); CALL cpm_assert_student_json_write(NEW.`commit_result`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_outside_exam_result_import_sessions_u`$$

CREATE TRIGGER `cpm_sd_outside_exam_result_import_sessions_u` BEFORE UPDATE ON `outside_exam_result_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); CALL cpm_assert_student_json_write(NEW.`commit_result`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_push_subscriptions_i`$$

CREATE TRIGGER `cpm_sd_push_subscriptions_i` BEFORE INSERT ON `push_subscriptions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_role_write(NEW.user_role,NEW.user_id,NULL); END$$

DROP TRIGGER IF EXISTS `cpm_sd_push_subscriptions_u`$$

CREATE TRIGGER `cpm_sd_push_subscriptions_u` BEFORE UPDATE ON `push_subscriptions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_role_write(OLD.user_role,OLD.user_id,NULL); CALL cpm_assert_student_role_write(NEW.user_role,NEW.user_id,NULL); END$$

DROP TRIGGER IF EXISTS `cpm_sd_rating_recalc_jobs_i`$$

CREATE TRIGGER `cpm_sd_rating_recalc_jobs_i` BEFORE INSERT ON `rating_recalc_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`errors`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_rating_recalc_jobs_u`$$

CREATE TRIGGER `cpm_sd_rating_recalc_jobs_u` BEFORE UPDATE ON `rating_recalc_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`errors`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_rating_recalc_staging_i`$$

CREATE TRIGGER `cpm_sd_rating_recalc_staging_i` BEFORE INSERT ON `rating_recalc_staging` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_rating_recalc_staging_u`$$

CREATE TRIGGER `cpm_sd_rating_recalc_staging_u` BEFORE UPDATE ON `rating_recalc_staging` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_card_progress_i`$$

CREATE TRIGGER `cpm_sd_student_card_progress_i` BEFORE INSERT ON `student_card_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_card_progress_u`$$

CREATE TRIGGER `cpm_sd_student_card_progress_u` BEFORE UPDATE ON `student_card_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_credentials_i`$$

CREATE TRIGGER `cpm_sd_student_credentials_i` BEFORE INSERT ON `student_credentials` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_credentials_u`$$

CREATE TRIGGER `cpm_sd_student_credentials_u` BEFORE UPDATE ON `student_credentials` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_progress_i`$$

CREATE TRIGGER `cpm_sd_student_progress_i` BEFORE INSERT ON `student_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_progress_u`$$

CREATE TRIGGER `cpm_sd_student_progress_u` BEFORE UPDATE ON `student_progress` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_section_study_settings_i`$$

CREATE TRIGGER `cpm_sd_student_section_study_settings_i` BEFORE INSERT ON `student_section_study_settings` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_student_section_study_settings_u`$$

CREATE TRIGGER `cpm_sd_student_section_study_settings_u` BEFORE UPDATE ON `student_section_study_settings` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_students_i`$$

CREATE TRIGGER `cpm_sd_students_i` AFTER INSERT ON `students` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_students_u`$$

CREATE TRIGGER `cpm_sd_students_u` BEFORE UPDATE ON `students` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`id`); CALL cpm_assert_student_write(NEW.`id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_telegram_bot_chats_i`$$

CREATE TRIGGER `cpm_sd_telegram_bot_chats_i` BEFORE INSERT ON `telegram_bot_chats` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_telegram_bot_chats_u`$$

CREATE TRIGGER `cpm_sd_telegram_bot_chats_u` BEFORE UPDATE ON `telegram_bot_chats` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_test_sessions_i`$$

CREATE TRIGGER `cpm_sd_test_sessions_i` BEFORE INSERT ON `test_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_test_sessions_u`$$

CREATE TRIGGER `cpm_sd_test_sessions_u` BEFORE UPDATE ON `test_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_job_results_i`$$

CREATE TRIGGER `cpm_sd_user_import_job_results_i` BEFORE INSERT ON `user_import_job_results` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`rows_data`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_job_results_u`$$

CREATE TRIGGER `cpm_sd_user_import_job_results_u` BEFORE UPDATE ON `user_import_job_results` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`rows_data`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_jobs_i`$$

CREATE TRIGGER `cpm_sd_user_import_jobs_i` BEFORE INSERT ON `user_import_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`errors`); CALL cpm_assert_student_json_write(NEW.`entities_created`); CALL cpm_assert_student_json_write(NEW.`summary`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_jobs_u`$$

CREATE TRIGGER `cpm_sd_user_import_jobs_u` BEFORE UPDATE ON `user_import_jobs` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`errors`); CALL cpm_assert_student_json_write(NEW.`entities_created`); CALL cpm_assert_student_json_write(NEW.`summary`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_sessions_i`$$

CREATE TRIGGER `cpm_sd_user_import_sessions_i` BEFORE INSERT ON `user_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_user_import_sessions_u`$$

CREATE TRIGGER `cpm_sd_user_import_sessions_u` BEFORE UPDATE ON `user_import_sessions` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_json_write(NEW.`preview_payload`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zap_dates_i`$$

CREATE TRIGGER `cpm_sd_zap_dates_i` BEFORE INSERT ON `zap_dates` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=NEW.`zap_id` LOCK IN SHARE MODE; IF NEW.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zap_dates_u`$$

CREATE TRIGGER `cpm_sd_zap_dates_u` BEFORE UPDATE ON `zap_dates` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=OLD.`zap_id` LOCK IN SHARE MODE; IF OLD.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=NEW.`zap_id` LOCK IN SHARE MODE; IF NEW.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zap_img_i`$$

CREATE TRIGGER `cpm_sd_zap_img_i` BEFORE INSERT ON `zap_img` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=NEW.`zap_id` LOCK IN SHARE MODE; IF NEW.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zap_img_u`$$

CREATE TRIGGER `cpm_sd_zap_img_u` BEFORE UPDATE ON `zap_img` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=OLD.`zap_id` LOCK IN SHARE MODE; IF OLD.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); CALL cpm_assert_student_write(NULL); SET owner_id=NULL; SELECT `student_id` INTO owner_id FROM `zaps` WHERE id=NEW.`zap_id` LOCK IN SHARE MODE; IF NEW.`zap_id` IS NOT NULL AND owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF; CALL cpm_assert_student_write(owner_id); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zaps_i`$$

CREATE TRIGGER `cpm_sd_zaps_i` BEFORE INSERT ON `zaps` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(NEW.`student_id`); END$$

DROP TRIGGER IF EXISTS `cpm_sd_zaps_u`$$

CREATE TRIGGER `cpm_sd_zaps_u` BEFORE UPDATE ON `zaps` FOR EACH ROW BEGIN DECLARE owner_id BIGINT DEFAULT NULL; CALL cpm_assert_student_write(OLD.`student_id`); CALL cpm_assert_student_write(NEW.`student_id`); END$$
DELIMITER ;
