-- Run only through fence.apply_write_barrier_migration after backend030 and
-- homework002. No runtime/startup DDL. Installer validates the actual ownership
-- schema, creates per-table guards, then checks every trigger definition.
-- MySQL8 / InnoDB required. DDL requires a maintenance window with all writers
-- stopped; GET_LOCK alone cannot drain an older, uninstrumented Mongo process.

CREATE TABLE IF NOT EXISTS student_deletion_guard_state (
  id TINYINT NOT NULL PRIMARY KEY,
  schema_version INT NOT NULL,
  CONSTRAINT ck_student_deletion_guard_singleton CHECK (id=1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO student_deletion_guard_state(id,schema_version) VALUES(1,1);

DROP PROCEDURE IF EXISTS cpm_assert_student_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_write(IN p_student_id BIGINT)
BEGIN
  DECLARE guard_version INT DEFAULT NULL;
  DECLARE blocked_job CHAR(36) DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET blocked_job=NULL;
  -- Current locking read, transaction-held even under READ COMMITTED. A barrier
  -- insertion takes the exclusive singleton lock before becoming visible.
  SELECT schema_version INTO guard_version FROM student_deletion_guard_state
    WHERE id=1 LOCK IN SHARE MODE;
  IF guard_version IS NULL OR guard_version<>1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_guard_unavailable';
  END IF;
  IF p_student_id IS NOT NULL THEN
    SELECT job_id INTO blocked_job FROM student_deletion_barriers
      WHERE student_id=p_student_id LOCK IN SHARE MODE;
    IF blocked_job IS NOT NULL THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_pending';
    END IF;
  END IF;
END//
DELIMITER ;


-- Shared journals allow redaction (NEW values only) but cannot reintroduce
-- revoked explicit IDs from a stale in-memory preview/result snapshot.
DROP PROCEDURE IF EXISTS cpm_assert_student_journal_id;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_journal_id(IN p_value JSON)
BEGIN
  DECLARE normalized JSON;
  DECLARE scalar_text LONGTEXT;
  DECLARE owner_id BIGINT DEFAULT NULL;
  SET normalized=p_value;
  IF p_value IS NOT NULL AND JSON_TYPE(p_value)='STRING' THEN
    SET scalar_text=JSON_UNQUOTE(p_value);
    IF NOT REGEXP_LIKE(scalar_text,'^[0-9]+$','c') THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid';
    END IF;
    SET scalar_text=TRIM(LEADING '0' FROM scalar_text);
    IF scalar_text='' THEN SET scalar_text='0'; END IF;
    SET normalized=JSON_QUOTE(scalar_text);
  END IF;
  CALL cpm_assert_student_json_id(normalized,FALSE,owner_id);
  CALL cpm_assert_student_write(owner_id);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_json_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_json_write(IN p_document JSON)
BEGIN
  DECLARE paths JSON;
  DECLARE matches_json JSON;
  DECLARE value_json JSON;
  DECLARE path_index INT DEFAULT 0;
  DECLARE match_index INT DEFAULT 0;
  DECLARE item_index INT DEFAULT 0;
  CALL cpm_assert_student_write(NULL);
  -- Explicit ownership paths only; generic staff/exam/id fields never count.
  -- Digit strings are normalized exactly as import/retention ownership parsers.
  SET paths=JSON_ARRAY('$**.student_id','$**.studentId','$**.studentID',
    '$**.existing_student_id','$**.target_student_id','$**.privilegeBaselineStudentId',
    '$**.student.id','$**.student_ids','$**.studentIds','$**.student_ids_created');
  IF p_document IS NOT NULL THEN
    WHILE path_index<JSON_LENGTH(paths) DO
      SET matches_json=JSON_EXTRACT(p_document,JSON_UNQUOTE(JSON_EXTRACT(paths,CONCAT('$[',path_index,']'))));
      SET match_index=0;
      WHILE matches_json IS NOT NULL AND match_index<JSON_LENGTH(matches_json) DO
        SET value_json=JSON_EXTRACT(matches_json,CONCAT('$[',match_index,']'));
        IF path_index<7 THEN
          CALL cpm_assert_student_journal_id(value_json);
        ELSEIF JSON_TYPE(value_json)<>'NULL' THEN
          IF JSON_TYPE(value_json)<>'ARRAY' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid'; END IF;
          SET item_index=0;
          WHILE item_index<JSON_LENGTH(value_json) DO
            CALL cpm_assert_student_journal_id(JSON_EXTRACT(value_json,CONCAT('$[',item_index,']')));
            SET item_index=item_index+1;
          END WHILE;
        END IF;
        SET match_index=match_index+1;
      END WHILE;
      SET path_index=path_index+1;
    END WHILE;
  END IF;
END//
DELIMITER ;

-- Retired homework/realtime data still has writers in older deployments. Every
-- referenced row is a current locking read, including schemas with no FK.
DROP PROCEDURE IF EXISTS cpm_assert_student_role_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_role_write(IN p_role VARCHAR(32), IN p_id BIGINT, IN p_owner BIGINT)
BEGIN
  CALL cpm_assert_student_write(NULL);
  IF p_role IS NULL OR BINARY p_role NOT IN ('student','admin','proctor','examinator','supervisor','staff_admin') OR p_id IS NULL OR p_id<1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_role_invalid';
  END IF;
  IF BINARY p_role='student' THEN
    IF p_owner IS NOT NULL AND p_owner<>p_id THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
    END IF;
    CALL cpm_assert_student_write(p_id);
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_thread_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_thread_write(IN p_thread BIGINT, OUT p_owner BIGINT)
BEGIN
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET p_owner=NULL;
  CALL cpm_assert_student_write(NULL);
  SET p_owner=NULL;
  SELECT student_id INTO p_owner FROM homework_chat_threads WHERE id=p_thread LOCK IN SHARE MODE;
  IF p_owner IS NULL OR p_owner<1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing';
  END IF;
  CALL cpm_assert_student_write(p_owner);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_message_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_message_write(IN p_message BIGINT, IN p_thread BIGINT, OUT p_owner BIGINT)
BEGIN
  DECLARE message_thread BIGINT DEFAULT NULL;
  DECLARE sender_role VARCHAR(32) DEFAULT NULL;
  DECLARE sender_id BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET message_thread=NULL;
  CALL cpm_assert_student_write(NULL);
  SELECT thread_id,m.sender_role,m.sender_id INTO message_thread,sender_role,sender_id
    FROM homework_chat_messages m WHERE id=p_message LOCK IN SHARE MODE;
  IF message_thread IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing';
  END IF;
  IF p_thread IS NOT NULL AND p_thread<>message_thread THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
  END IF;
  CALL cpm_assert_student_thread_write(message_thread,p_owner);
  IF BINARY sender_role='system' AND sender_id IS NULL THEN
    CALL cpm_assert_student_write(NULL);
  ELSE
    CALL cpm_assert_student_role_write(sender_role,sender_id,p_owner);
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_notification_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_notification_write(IN p_role VARCHAR(32), IN p_recipient BIGINT, IN p_student BIGINT, IN p_thread BIGINT, OUT p_owner BIGINT)
BEGIN
  CALL cpm_assert_student_write(NULL);
  SET p_owner=p_student;
  IF p_student IS NOT NULL AND p_student<1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_invalid';
  END IF;
  IF p_thread IS NOT NULL THEN
    CALL cpm_assert_student_thread_write(p_thread,p_owner);
    IF p_student IS NOT NULL AND p_student<>p_owner THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
    END IF;
  END IF;
  CALL cpm_assert_student_role_write(p_role,p_recipient,p_owner);
  IF BINARY p_role='student' THEN SET p_owner=p_recipient; END IF;
  CALL cpm_assert_student_write(p_owner);
END//
DELIMITER ;

-- IDs are accepted only as canonical integral JSON numbers or digit strings.
-- Never coerce booleans, fractions, objects, negative values, or overflow.
DROP PROCEDURE IF EXISTS cpm_assert_student_json_id;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_json_id(IN p_value JSON, IN p_allow_zero BOOLEAN, OUT p_id BIGINT)
BEGIN
  DECLARE scalar_text VARCHAR(128) DEFAULT NULL;
  SET p_id=NULL;
  IF p_value IS NOT NULL AND JSON_TYPE(p_value)<>'NULL' THEN
    IF JSON_TYPE(p_value) NOT IN ('INTEGER','STRING') THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid';
    END IF;
    SET scalar_text=JSON_UNQUOTE(p_value);
    IF NOT REGEXP_LIKE(scalar_text, '^(0|[1-9][0-9]*)$', 'c') OR CHAR_LENGTH(scalar_text)>19 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid';
    END IF;
    IF CAST(scalar_text AS DECIMAL(20,0))>9223372036854775807 OR (scalar_text='0' AND NOT p_allow_zero) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_json_id_invalid';
    END IF;
    SET p_id=CAST(scalar_text AS SIGNED);
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_outbox_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_outbox_write(IN p_thread BIGINT, IN p_event VARCHAR(48), IN p_entity BIGINT, IN p_payload JSON)
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  DECLARE linked_owner BIGINT DEFAULT NULL;
  DECLARE object_id BIGINT DEFAULT NULL;
  DECLARE payload_id BIGINT DEFAULT NULL;
  DECLARE actor_id BIGINT DEFAULT NULL;
  DECLARE homework_id BIGINT DEFAULT NULL;
  DECLARE stored_homework BIGINT DEFAULT NULL;
  DECLARE linked_thread BIGINT DEFAULT NULL;
  DECLARE room_id BIGINT DEFAULT NULL;
  DECLARE role_name VARCHAR(32) DEFAULT NULL;
  DECLARE room_role VARCHAR(32) DEFAULT NULL;
  DECLARE room_text VARCHAR(128) DEFAULT NULL;
  DECLARE job_id VARCHAR(64) DEFAULT NULL;
  DECLARE document JSON;
  DECLARE data_value JSON;
  DECLARE job_value JSON;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_owner=NULL;
  CALL cpm_assert_student_write(NULL);
  IF p_payload IS NULL THEN SET document=JSON_OBJECT(); ELSE SET document=p_payload; END IF;
  IF JSON_TYPE(document)<>'OBJECT' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
  END IF;
  IF p_thread IS NOT NULL THEN CALL cpm_assert_student_thread_write(p_thread,owner_id); END IF;
  IF BINARY p_event='message.created' THEN
    IF JSON_LENGTH(document)<>0 THEN
      IF JSON_LENGTH(document)<>1 OR NOT JSON_CONTAINS_PATH(document,'all','$.message_id') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.message_id'),FALSE,payload_id);
      IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF;
    END IF;
    IF p_entity IS NOT NULL AND payload_id IS NOT NULL AND p_entity<>payload_id THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
    END IF;
    SET object_id=COALESCE(p_entity,payload_id);
    IF object_id IS NULL OR object_id<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    CALL cpm_assert_student_message_write(object_id,p_thread,linked_owner);
  ELSEIF BINARY p_event='message.read' THEN
    IF p_thread IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    IF JSON_LENGTH(document)<>0 THEN
      IF JSON_LENGTH(document)<>3 OR NOT JSON_CONTAINS_PATH(document,'all','$.reader_role','$.reader_id','$.last_message_id') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.reader_id'),FALSE,actor_id);
      CALL cpm_assert_student_role_write(JSON_UNQUOTE(JSON_EXTRACT(document,'$.reader_role')),actor_id,owner_id);
      CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.last_message_id'),TRUE,payload_id);
      IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF;
    END IF;
    IF p_entity IS NOT NULL AND payload_id IS NOT NULL AND p_entity<>payload_id THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
    END IF;
    SET object_id=COALESCE(p_entity,payload_id);
    IF object_id IS NULL OR object_id<0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    IF object_id<>0 THEN CALL cpm_assert_student_message_write(object_id,p_thread,linked_owner); END IF;
  ELSEIF BINARY p_event='typing.changed' THEN
    IF p_thread IS NULL OR p_entity IS NOT NULL OR JSON_LENGTH(document)<>4
      OR NOT JSON_CONTAINS_PATH(document,'all','$.actor_role','$.actor_id','$.active','$.expires_in_ms')
      OR JSON_TYPE(JSON_EXTRACT(document,'$.active'))<>'BOOLEAN' THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
    END IF;
    CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.actor_id'),FALSE,actor_id);
    CALL cpm_assert_student_role_write(JSON_UNQUOTE(JSON_EXTRACT(document,'$.actor_role')),actor_id,owner_id);
    CALL cpm_assert_student_json_id(JSON_EXTRACT(document,'$.expires_in_ms'),FALSE,payload_id);
    IF payload_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF;
  ELSEIF BINARY p_event IN ('submission.changed','job.progress','notification.created') THEN
    IF p_thread IS NOT NULL OR JSON_LENGTH(document)<>2 OR NOT JSON_CONTAINS_PATH(document,'all','$.room','$.data')
      OR JSON_TYPE(JSON_EXTRACT(document,'$.room'))<>'STRING' OR JSON_TYPE(JSON_EXTRACT(document,'$.data'))<>'OBJECT' THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
    END IF;
    SET room_text=JSON_UNQUOTE(JSON_EXTRACT(document,'$.room'));
    IF NOT REGEXP_LIKE(room_text,'^user:(student|admin|proctor|examinator|supervisor|staff_admin):[1-9][0-9]*$','c') THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_room_invalid';
    END IF;
    SET room_role=SUBSTRING_INDEX(SUBSTRING_INDEX(room_text,':',2),':',-1);
    CALL cpm_assert_student_json_id(JSON_QUOTE(SUBSTRING_INDEX(room_text,':',-1)),FALSE,room_id);
    SET data_value=JSON_EXTRACT(document,'$.data');
    IF BINARY p_event='submission.changed' THEN
      IF BINARY room_role<>'student' OR JSON_LENGTH(data_value)<>3 OR NOT JSON_CONTAINS_PATH(data_value,'all','$.homework_id','$.student_id','$.submission_id') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.student_id'),FALSE,owner_id);
      CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.homework_id'),FALSE,homework_id);
      CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.submission_id'),FALSE,payload_id);
      IF owner_id IS NULL OR homework_id IS NULL OR NOT (p_entity<=>payload_id) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict';
      END IF;
      IF payload_id IS NOT NULL THEN
        SELECT student_id,s.homework_id INTO linked_owner,stored_homework FROM homework_submissions s WHERE id=payload_id LOCK IN SHARE MODE;
        IF linked_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
        IF owner_id<>linked_owner OR homework_id<>stored_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
      END IF;
    ELSEIF BINARY p_event='job.progress' THEN
      IF BINARY room_role<>'student' OR p_entity IS NOT NULL OR JSON_LENGTH(data_value)<>1
        OR NOT JSON_CONTAINS_PATH(data_value,'all','$.job') OR JSON_TYPE(JSON_EXTRACT(data_value,'$.job'))<>'OBJECT' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      SET job_value=JSON_EXTRACT(data_value,'$.job');
      IF JSON_LENGTH(job_value)<>6 OR NOT JSON_CONTAINS_PATH(job_value,'all','$.id','$.homework_id','$.status','$.stage','$.progress','$.error_code')
        OR JSON_TYPE(JSON_EXTRACT(job_value,'$.id'))<>'STRING'
        OR JSON_TYPE(JSON_EXTRACT(job_value,'$.status'))<>'STRING'
        OR JSON_TYPE(JSON_EXTRACT(job_value,'$.stage'))<>'STRING'
        OR JSON_TYPE(JSON_EXTRACT(job_value,'$.progress'))<>'INTEGER'
        OR JSON_TYPE(JSON_EXTRACT(job_value,'$.error_code')) NOT IN ('NULL','STRING')
        OR JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.status'))=''
        OR JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.stage'))='' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(job_value,'$.progress'),TRUE,payload_id);
      IF payload_id IS NULL OR payload_id>100 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid'; END IF;
      SET job_id=JSON_UNQUOTE(JSON_EXTRACT(job_value,'$.id'));
      IF NOT REGEXP_LIKE(job_id,'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$','c') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(job_value,'$.homework_id'),FALSE,homework_id);
      SELECT j.student_id,j.homework_id,s.student_id INTO linked_owner,stored_homework,owner_id
        FROM homework_file_jobs j JOIN homework_submissions s ON s.id=j.submission_id WHERE BINARY j.id=BINARY job_id LOCK IN SHARE MODE;
      IF linked_owner IS NULL OR owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
      IF homework_id IS NULL OR homework_id<>stored_homework OR owner_id<>linked_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
    ELSE
      IF JSON_LENGTH(data_value)<>1 OR NOT JSON_CONTAINS_PATH(data_value,'all','$.entity_id') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_payload_invalid';
      END IF;
      CALL cpm_assert_student_json_id(JSON_EXTRACT(data_value,'$.entity_id'),FALSE,payload_id);
      IF payload_id IS NULL OR NOT (p_entity<=>payload_id) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
      SET role_name=NULL;
      SELECT recipient_role,recipient_id,student_id,thread_id INTO role_name,actor_id,linked_owner,linked_thread
        FROM notifications WHERE id=payload_id LOCK IN SHARE MODE;
      IF role_name IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
      IF BINARY room_role<>BINARY role_name OR room_id<>actor_id THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
      CALL cpm_assert_student_notification_write(role_name,actor_id,linked_owner,linked_thread,owner_id);
    END IF;
    CALL cpm_assert_student_role_write(room_role,room_id,owner_id);
    CALL cpm_assert_student_write(owner_id);
  ELSE
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_outbox_event_unknown';
  END IF;
END//
DELIMITER ;

-- Captured command IDs survive as nonpersonal replay-denial tombstones. The
-- only OLD-owner exception requires the durable matching job and barrier.

-- Opaque object keys retain an owner even after source SQL rows are unlinked.
-- Current locking reads plus the singleton fence prevent raw SQL adoption by a
-- different student between purge validation and external-object deletion.
DROP PROCEDURE IF EXISTS cpm_assert_student_object_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_object_write(IN p_key VARCHAR(512), IN p_owner BIGINT, IN p_submission BIGINT)
BEGIN
  DECLARE hit_key VARCHAR(512) DEFAULT NULL;
  DECLARE referenced_owner BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET hit_key=NULL;
  CALL cpm_assert_student_write(p_owner);
  IF p_key IS NOT NULL THEN
    IF p_key='' OR p_owner IS NULL OR p_owner<1 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_invalid'; END IF;
    SET hit_key=NULL;
    SELECT o.object_key,o.student_id INTO hit_key,referenced_owner FROM homework_object_ownership o
      WHERE BINARY o.object_key=BINARY p_key AND (o.student_id<>p_owner OR (o.submission_id IS NOT NULL AND p_submission IS NOT NULL AND o.submission_id<>p_submission))
      LIMIT 1 LOCK IN SHARE MODE;
    IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF;
    SET hit_key=NULL;
    SELECT f.object_key,s.student_id INTO hit_key,referenced_owner FROM homework_submission_files f LEFT JOIN homework_submissions s ON s.id=f.submission_id
      WHERE BINARY f.object_key=BINARY p_key AND (s.id IS NULL OR s.student_id<>p_owner OR (p_submission IS NOT NULL AND f.submission_id<>p_submission))
      LIMIT 1 LOCK IN SHARE MODE;
    IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF;
    SET hit_key=NULL;
    SELECT j.id,j.student_id INTO hit_key,referenced_owner FROM homework_file_jobs j LEFT JOIN homework_submissions s ON s.id=j.submission_id
      WHERE (BINARY j.staging_key=BINARY p_key OR BINARY j.processed_output_key=BINARY p_key)
        AND (s.id IS NULL OR j.student_id<>p_owner OR s.student_id<>p_owner OR j.homework_id<>s.homework_id OR (p_submission IS NOT NULL AND j.submission_id<>p_submission))
      LIMIT 1 LOCK IN SHARE MODE;
    IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF;
    SET hit_key=NULL;
    SELECT o.object_key,p.student_id INTO hit_key,referenced_owner FROM homework_student_purge_objects o LEFT JOIN homework_student_purges p ON BINARY p.job_id=BINARY o.job_id
      WHERE BINARY o.object_key=BINARY p_key AND (p.job_id IS NULL OR p.student_id<>p_owner)
      LIMIT 1 LOCK IN SHARE MODE;
    IF hit_key IS NOT NULL THEN CALL cpm_assert_student_write(referenced_owner); SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_object_owner_conflict'; END IF;
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_file_pointer;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_file_pointer(IN p_file BIGINT, IN p_submission BIGINT, IN p_owner BIGINT)
BEGIN
  DECLARE file_submission BIGINT DEFAULT NULL;
  DECLARE file_key VARCHAR(512) DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET file_submission=NULL;
  CALL cpm_assert_student_write(p_owner);
  IF p_file IS NOT NULL THEN
    SELECT submission_id,object_key INTO file_submission,file_key FROM homework_submission_files WHERE id=p_file LOCK IN SHARE MODE;
    IF file_submission IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    IF p_submission IS NULL OR p_submission<>file_submission THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_file_submission_conflict'; END IF;
    CALL cpm_assert_student_object_write(file_key,p_owner,p_submission);
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_submission_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_submission_write(IN p_submission BIGINT, IN p_owner BIGINT, IN p_draft BIGINT, IN p_current BIGINT)
BEGIN
  CALL cpm_assert_student_write(p_owner);
  CALL cpm_assert_student_file_pointer(p_draft,p_submission,p_owner);
  CALL cpm_assert_student_file_pointer(p_current,p_submission,p_owner);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_file_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_file_write(IN p_submission BIGINT, IN p_key VARCHAR(512))
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL;
  CALL cpm_assert_student_write(NULL);
  SELECT student_id INTO owner_id FROM homework_submissions WHERE id=p_submission LOCK IN SHARE MODE;
  IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_write(owner_id);
  CALL cpm_assert_student_object_write(p_key,owner_id,p_submission);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_job_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_job_write(IN p_job VARCHAR(36), IN p_owner BIGINT, IN p_submission BIGINT, IN p_homework BIGINT, IN p_result BIGINT, IN p_staging VARCHAR(512), IN p_processed VARCHAR(512))
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  DECLARE homework_id BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL;
  CALL cpm_assert_student_write(p_owner);
  SELECT s.student_id,s.homework_id INTO owner_id,homework_id FROM homework_submissions s WHERE s.id=p_submission LOCK IN SHARE MODE;
  IF owner_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_write(owner_id);
  IF owner_id<>p_owner OR homework_id<>p_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
  CALL cpm_assert_student_file_pointer(p_result,p_submission,p_owner);
  CALL cpm_assert_student_object_write(p_staging,p_owner,p_submission);
  CALL cpm_assert_student_object_write(p_processed,p_owner,p_submission);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_ledger_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_ledger_write(IN p_key VARCHAR(512), IN p_owner BIGINT, IN p_submission BIGINT, IN p_job VARCHAR(36))
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  DECLARE parent_owner BIGINT DEFAULT NULL;
  DECLARE job_homework BIGINT DEFAULT NULL;
  DECLARE parent_homework BIGINT DEFAULT NULL;
  DECLARE job_submission BIGINT DEFAULT NULL;
  DECLARE resolved_submission BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET owner_id=NULL;
  CALL cpm_assert_student_write(p_owner);
  SET resolved_submission=p_submission;
  IF p_submission IS NOT NULL THEN
    SELECT student_id INTO owner_id FROM homework_submissions WHERE id=p_submission LOCK IN SHARE MODE;
    IF owner_id IS NOT NULL THEN
      CALL cpm_assert_student_write(owner_id);
      IF owner_id<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
    END IF;
  END IF;
  IF p_job IS NOT NULL THEN
    SET owner_id=NULL;
    SELECT j.student_id,j.submission_id,s.student_id,j.homework_id,s.homework_id INTO owner_id,job_submission,parent_owner,job_homework,parent_homework
      FROM homework_file_jobs j LEFT JOIN homework_submissions s ON s.id=j.submission_id WHERE BINARY j.id=BINARY p_job LOCK IN SHARE MODE;
    IF owner_id IS NOT NULL THEN
      CALL cpm_assert_student_write(owner_id);
      IF owner_id<>p_owner OR (p_submission IS NOT NULL AND p_submission<>job_submission) THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
      IF parent_owner IS NOT NULL THEN
        CALL cpm_assert_student_write(parent_owner);
        IF parent_owner<>p_owner OR job_homework<>parent_homework THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
      END IF;
      SET resolved_submission=job_submission;
    END IF;
  END IF;
  CALL cpm_assert_student_object_write(p_key,p_owner,resolved_submission);
END//
DELIMITER ;


DROP PROCEDURE IF EXISTS cpm_assert_student_attempt_owner;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_attempt_owner(IN p_attempt BIGINT, OUT p_owner BIGINT)
BEGIN
  DECLARE assigned_owner BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET p_owner=NULL;
  CALL cpm_assert_student_write(NULL);
  SET p_owner=NULL;
  SELECT a.student_id,s.student_id INTO p_owner,assigned_owner FROM classic_exam_attempts a LEFT JOIN classic_exam_assignments s ON s.id=a.assignment_id
    WHERE a.id=p_attempt LOCK IN SHARE MODE;
  IF p_owner IS NULL OR assigned_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_write(p_owner);
  CALL cpm_assert_student_write(assigned_owner);
  IF p_owner<>assigned_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_question_link;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_question_link(IN p_question BIGINT, IN p_attempt BIGINT)
BEGIN
  DECLARE linked_attempt BIGINT DEFAULT NULL;
  DECLARE owner_id BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_attempt=NULL;
  CALL cpm_assert_student_write(NULL);
  IF p_question IS NOT NULL THEN
    SELECT attempt_id INTO linked_attempt FROM classic_exam_presented_questions WHERE id=p_question LOCK IN SHARE MODE;
    IF linked_attempt IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    CALL cpm_assert_student_attempt_owner(linked_attempt,owner_id);
    IF p_attempt IS NULL OR linked_attempt<>p_attempt THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_question_attempt_conflict'; END IF;
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_attempt_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_attempt_write(IN p_attempt BIGINT, IN p_owner BIGINT, IN p_assignment BIGINT, IN p_question BIGINT)
BEGIN
  DECLARE assigned_owner BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET assigned_owner=NULL;
  CALL cpm_assert_student_write(p_owner);
  SELECT student_id INTO assigned_owner FROM classic_exam_assignments WHERE id=p_assignment LOCK IN SHARE MODE;
  IF assigned_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_write(assigned_owner);
  IF assigned_owner<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
  CALL cpm_assert_student_question_link(p_question,p_attempt);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_presented_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_presented_write(IN p_attempt BIGINT, IN p_replaces BIGINT)
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  CALL cpm_assert_student_attempt_owner(p_attempt,owner_id);
  CALL cpm_assert_student_question_link(p_replaces,p_attempt);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_round_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_round_write(IN p_attempt BIGINT, IN p_question BIGINT)
BEGIN
  DECLARE owner_id BIGINT DEFAULT NULL;
  CALL cpm_assert_student_attempt_owner(p_attempt,owner_id);
  IF p_question IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_question_link(p_question,p_attempt);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_vote_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_vote_write(IN p_round BIGINT)
BEGIN
  DECLARE attempt_id BIGINT DEFAULT NULL;
  DECLARE question_id BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET attempt_id=NULL;
  CALL cpm_assert_student_write(NULL);
  SELECT r.attempt_id,r.presented_question_id INTO attempt_id,question_id FROM classic_exam_vote_rounds r WHERE id=p_round LOCK IN SHARE MODE;
  IF attempt_id IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
  CALL cpm_assert_student_round_write(attempt_id,question_id);
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_attendance_write;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_attendance_write(IN p_owner BIGINT, IN p_zap BIGINT)
BEGIN
  DECLARE linked_owner BIGINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET linked_owner=NULL;
  CALL cpm_assert_student_write(p_owner);
  IF p_zap IS NOT NULL THEN
    SELECT student_id INTO linked_owner FROM zaps WHERE id=p_zap LOCK IN SHARE MODE;
    IF linked_owner IS NULL THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_missing'; END IF;
    CALL cpm_assert_student_write(linked_owner);
    IF linked_owner<>p_owner THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_owner_conflict'; END IF;
  END IF;
END//
DELIMITER ;

DROP PROCEDURE IF EXISTS cpm_assert_student_command_erasure;
DELIMITER //
CREATE PROCEDURE cpm_assert_student_command_erasure(IN p_command BIGINT, OUT p_captured BOOLEAN, OUT p_erasure BOOLEAN)
BEGIN
  DECLARE job_status VARCHAR(16) DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET job_status=NULL;
  CALL cpm_assert_student_write(NULL);
  SET p_captured=FALSE;
  SET p_erasure=FALSE;
  SELECT j.status INTO job_status
    FROM student_deletion_jobs j JOIN student_deletion_barriers b ON b.job_id=j.id AND b.student_id=j.student_id
    WHERE JSON_TYPE(JSON_EXTRACT(j.manifest_json,'$.command_ids'))='ARRAY'
      AND JSON_CONTAINS(JSON_EXTRACT(j.manifest_json,'$.command_ids'),CAST(p_command AS JSON))
    LIMIT 1 LOCK IN SHARE MODE;
  IF job_status IS NOT NULL THEN
    SET p_captured=TRUE;
    SET p_erasure=(BINARY job_status IN ('queued','running','waiting','failed'));
  END IF;
END//
DELIMITER ;

DROP TRIGGER IF EXISTS cpm_sdb_insert;
DELIMITER //
CREATE TRIGGER cpm_sdb_insert BEFORE INSERT ON student_deletion_barriers
FOR EACH ROW
BEGIN
  DECLARE guard_version INT DEFAULT NULL;
  SELECT schema_version INTO guard_version FROM student_deletion_guard_state
    WHERE id=1 FOR UPDATE;
  IF guard_version IS NULL OR guard_version<>1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_write_guard_unavailable';
  END IF;
END//
DELIMITER ;

DROP TRIGGER IF EXISTS cpm_sdb_update;
CREATE TRIGGER cpm_sdb_update BEFORE UPDATE ON student_deletion_barriers
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_barrier_immutable';

DROP TRIGGER IF EXISTS cpm_sdb_delete;
CREATE TRIGGER cpm_sdb_delete BEFORE DELETE ON student_deletion_barriers
FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='student_deletion_barrier_immutable';
