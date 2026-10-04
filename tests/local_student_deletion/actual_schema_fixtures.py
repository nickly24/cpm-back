"""Synthetic rows only, on the exact 79-table audited schema."""
import ast
import json


def add_checks(sql,checks,assert_sql,denied,gen):
    sql += [
        "INSERT INTO students(id,full_name,`class`,tg_name) VALUES(101,'Synthetic erased',10,'synthetic_one'),(202,'Synthetic retained',11,'synthetic_two')",
        "INSERT INTO auth_users(id,username,ref_id,role) VALUES(1,'synthetic_one',101,'student'),(2,'synthetic_two',202,'student'),(3,'synthetic_staff',101,'proctor')",
        "INSERT INTO homework(id,name,type,deadline) VALUES(1,'Synthetic homework',"+gen.literal('ОВ')+",'2099-01-01')",
        "INSERT INTO homework_submissions(id,homework_id,student_id) VALUES(1001,1,101),(2002,1,202)",
        "INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(11,1001,'synthetic/one.pdf','current',10,1,REPEAT('1',64)),(22,2002,'synthetic/two.pdf','current',20,2,REPEAT('2',64))",
        "UPDATE homework_submissions SET current_file_id=11 WHERE id=1001",
        "UPDATE homework_submissions SET current_file_id=22 WHERE id=2002",
        "INSERT INTO homework_file_jobs(id,client_upload_id,submission_id,homework_id,student_id,staging_key,processed_output_key,result_file_id) VALUES('11111111-1111-4111-8111-111111111111','31111111-1111-4111-8111-111111111111',1001,1,101,'synthetic/stage-one.pdf','synthetic/one.pdf',11),('22222222-2222-4222-8222-222222222222','32222222-2222-4222-8222-222222222222',2002,1,202,'synthetic/stage-two.pdf','synthetic/two.pdf',22)",
        "INSERT INTO homework_object_ownership(object_key,student_id,submission_id,upload_job_id,kind) VALUES('synthetic/one.pdf',101,1001,'11111111-1111-4111-8111-111111111111','processed'),('synthetic/two.pdf',202,2002,'22222222-2222-4222-8222-222222222222','processed')",
        "INSERT INTO homework_chat_threads(id,homework_id,student_id) VALUES(1001,1,101),(2002,1,202)",
        "INSERT INTO homework_chat_messages(id,thread_id,sender_role,sender_id,body) VALUES(11,1001,'student',101,'Synthetic erased message'),(22,2002,'student',202,'Synthetic retained message'),(23,2002,'proctor',101,'Synthetic staff collision')",
        "INSERT INTO homework_chat_reads(thread_id,reader_role,reader_id,last_message_id) VALUES(1001,'student',101,11),(2002,'student',202,22),(2002,'proctor',101,23)",
        "INSERT INTO homework_chat_admin_followers(thread_id,admin_id) VALUES(1001,1),(2002,1)",
        "INSERT INTO homework_realtime_presence(thread_id,actor_role,actor_id,expires_at) VALUES(1001,'student',101,'2099-01-01'),(2002,'student',202,'2099-01-01'),(2002,'proctor',101,'2099-01-01')",
        "INSERT INTO notifications(id,recipient_role,recipient_id,kind,student_id,thread_id) VALUES(31,'student',101,'synthetic',NULL,NULL),(32,'proctor',101,'synthetic',101,1001),(33,'student',202,'synthetic',202,2002),(34,'proctor',101,'synthetic',202,2002)",
        "INSERT INTO push_subscriptions(id,user_role,user_id,endpoint_hash,endpoint,p256dh,auth_secret) VALUES(31,'student',101,REPEAT('1',64),'https://synthetic.invalid/one','synthetic','synthetic'),(32,'proctor',101,REPEAT('2',64),'https://synthetic.invalid/staff','synthetic','synthetic'),(33,'student',202,REPEAT('3',64),'https://synthetic.invalid/two','synthetic','synthetic')",
        "INSERT INTO directions(id,name) VALUES(1,'Synthetic shared direction')",
        "INSERT INTO exams(id,direction_id,exam_type) VALUES(1,1,'classic')",
        "INSERT INTO classic_exam_assignments(id,exam_id,student_id,created_by_role,created_by,commission_name_snapshot) VALUES(1,1,101,'admin',1,'Synthetic'),(2,1,202,'admin',1,'Synthetic')",
        "INSERT INTO classic_exam_attempts(id,exam_id,student_id,assignment_id,attempt_no,history_generation,student_name_snapshot) VALUES(1,1,101,1,1,1,'Synthetic erased'),(2,1,202,2,1,1,'Synthetic retained')",
        "INSERT INTO classic_exam_attempt_part_progress(attempt_id,source_part_id,part_code,question_weight,required_count) VALUES(1,1,'A',1,2),(2,1,'A',1,2)",
        "INSERT INTO classic_exam_definition_versions(id,exam_id,config_version,config_json) VALUES(1,1,1,JSON_OBJECT())",
        "INSERT INTO classic_exam_definition_questions(id,definition_id,source_question_id,source_part_id,part_code,question_text,answer_text) VALUES(1,1,1,1,'A','Synthetic question','Synthetic answer')",
        "INSERT INTO classic_exam_presented_questions(id,attempt_id,definition_question_id,sequence_no,purpose,cycle_no) VALUES(11,1,1,1,'regular',1),(22,2,1,1,'regular',1)",
        "UPDATE classic_exam_attempts SET current_presented_question_id=11 WHERE id=1",
        "UPDATE classic_exam_attempts SET current_presented_question_id=22 WHERE id=2",
        "INSERT INTO classic_exam_vote_rounds(id,attempt_id,presented_question_id,round_no) VALUES(1,1,11,1),(2,2,22,1)",
        "INSERT INTO examinators(id,full_name) VALUES(1,'Synthetic shared examiner')",
        "INSERT INTO classic_exam_votes(id,round_id,examinator_id,value) VALUES(1,1,1,1),(2,2,1,0.5)",
        "INSERT INTO zaps(id,student_id,text,status,answer) VALUES(1,101,'Synthetic','apr','Synthetic'),(2,202,'Synthetic','apr','Synthetic')",
        "INSERT INTO class_days(id,`date`) VALUES(1,'2099-01-01')",
        "INSERT INTO attendance_types(id,code,name_ru) VALUES(1,'synthetic','Synthetic')",
        "INSERT INTO class_day_attendance(id,class_day_id,student_id,attendance_type_id,zap_id) VALUES(1,1,101,1,1),(2,1,202,1,2)",
        "INSERT INTO exam_admin_commands(id,actor_role,actor_id,idempotency_key,command_type,target_exam_id,target_student_id,target_attempt_id,request_hash,receipt,expires_at) VALUES(1,'admin',1,'51111111-1111-4111-8111-111111111111','synthetic',1,101,1,REPEAT('1',64),JSON_OBJECT('student_id',101),'2099-01-01'),(2,'admin',1,'52222222-2222-4222-8222-222222222222','synthetic',1,202,2,REPEAT('2',64),JSON_OBJECT('student_id',202),'2099-01-01'),(3,'admin',1,'53333333-3333-4333-8333-333333333333','synthetic',1,NULL,NULL,REPEAT('3',64),JSON_OBJECT('privilegeBaselineStudentId',101),'2099-01-01')",
        "INSERT INTO user_import_sessions(id,preview_payload,expires_at) VALUES(1,JSON_OBJECT('student_id',101),'2099-01-01'),(2,JSON_OBJECT('student_id',202),'2099-01-01')",
        "INSERT INTO attendance(id,`date`,student_id) VALUES(1,'2099-01-01',101),(2,'2099-01-01',202)",
        "INSERT INTO student_credentials(student_id,login) VALUES(101,'synthetic_one'),(202,'synthetic_two')",
    ]
    from actual_purge_sql_cases import add_workflow_file_checks
    add_workflow_file_checks(sql,checks,assert_sql,denied,gen)
    # Legitimate patched remove-draft/resubmit order clears all references
    # before deleting the file; every pointer guard remains strict.
    sql += [
        "INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(24,2002,'synthetic/draft-two.pdf','draft',10,1,REPEAT('4',64))",
        "UPDATE homework_submissions SET draft_file_id=24 WHERE id=2002",
        "UPDATE homework_submissions SET draft_file_id=NULL WHERE id=2002",
        "DELETE FROM homework_submission_files WHERE id=24",
        "UPDATE homework_file_jobs SET result_file_id=NULL WHERE student_id=202",
        "UPDATE homework_submissions SET current_file_id=NULL WHERE id=2002",
        "DELETE FROM homework_submission_files WHERE id=22",
    ]
    assert_sql('(SELECT draft_file_id IS NULL AND current_file_id IS NULL FROM homework_submissions WHERE id=2002)','allowed_remove_draft_resubmit_clear')
    assert_sql('(SELECT result_file_id IS NULL FROM homework_file_jobs WHERE student_id=202)','allowed_result_pointer_clear')
    sql += [
        "INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(22,2002,'synthetic/two.pdf','current',20,2,REPEAT('2',64))",
        "UPDATE homework_submissions SET current_file_id=22 WHERE id=2002",
        "UPDATE homework_file_jobs SET result_file_id=22 WHERE student_id=202",
    ]
    # Execute the exact aggregate-query builder, without importing app/config.
    source=gen.BACKEND/'cpm_back/services/student_deletion/file_consistency.py'
    nodes=[n for n in ast.parse(source.read_text()).body if isinstance(n,(ast.Assign,ast.FunctionDef))]
    namespace={'FileConsistencyError':RuntimeError}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
    def consistency_queries(scope=101):
        class Capture:
            def __init__(self):self.queries=[]
            def execute(self,query,params=()):
                for param in params:query=query.replace('%s',str(int(param)),1)
                self.queries.append(query)
            def fetchone(self):return {'count':0}
        cursor=Capture()
        result=namespace['aggregate_file_consistency'](cursor,scope,namespace['REQUIRED_COLUMNS'])
        return dict(zip(result['counts'],cursor.queries))
    for name,query in consistency_queries().items():
        assert_sql('('+query+')=0','file_clean_'+name)
    for pointer in ('current_file_id','draft_file_id'):
        denied(f'UPDATE homework_submissions SET {pointer}=11 WHERE id=2002',
               'incoming_'+pointer+'_guarded','student_file_submission_conflict')
    denied("UPDATE homework_file_jobs SET result_file_id=11 WHERE student_id=202",
           'incoming_job_result_guarded','student_file_submission_conflict')
    denied("UPDATE homework_file_jobs SET processed_output_key='synthetic/one.pdf' WHERE student_id=202",
           'shared_key_owner_guarded','student_object_owner_conflict')

    denied("UPDATE classic_exam_attempts SET current_presented_question_id=11 WHERE id=2",'classic_cross_current_question_denied','student_question_attempt_conflict')
    denied("UPDATE classic_exam_presented_questions SET replaces_presented_question_id=11 WHERE id=22",'classic_cross_replacement_denied','student_question_attempt_conflict')
    denied("UPDATE classic_exam_vote_rounds SET presented_question_id=11 WHERE id=2",'classic_cross_round_question_denied','student_question_attempt_conflict')
    denied("UPDATE classic_exam_attempts SET assignment_id=1 WHERE id=2",'classic_cross_assignment_denied','student_write_owner_conflict')
    denied("UPDATE class_day_attendance SET zap_id=1 WHERE id=2",'cross_attendance_zap_denied','student_write_owner_conflict')

    def outbox(ident,event,thread,entity,payload):
        return 'INSERT INTO homework_realtime_outbox(id,thread_id,event_type,entity_id,payload_json,expires_at) VALUES('+','.join([str(ident),'NULL' if thread is None else str(thread),gen.literal(event),'NULL' if entity is None else str(entity),gen.literal(json.dumps(payload,separators=(',',':'))),"'2099-01-01'"])+')'
    events=[
        ('message.created',1001,11,{'message_id':11}),
        ('message.read',1001,11,{'reader_role':'student','reader_id':101,'last_message_id':11}),
        ('typing.changed',1001,None,{'actor_role':'student','actor_id':101,'active':True,'expires_in_ms':1000}),
        ('submission.changed',None,1001,{'room':'user:student:101','data':{'homework_id':1,'student_id':101,'submission_id':1001}}),
        ('job.progress',None,None,{'room':'user:student:101','data':{'job':{'id':'11111111-1111-4111-8111-111111111111','homework_id':1,'status':'queued','stage':'checking','progress':1,'error_code':None}}}),
        ('notification.created',None,32,{'room':'user:proctor:101','data':{'entity_id':32}}),
    ]
    for i,event in enumerate(events,1):sql.append(outbox(i,*event))
    sql.append(outbox(20,'notification.created',None,34,{'room':'user:proctor:101','data':{'entity_id':34}}))
    denied("INSERT INTO homework_realtime_presence(thread_id,actor_role,actor_id,expires_at) VALUES(999,'proctor',101,'2099-01-01')",'no_fk_orphan_presence','student_write_owner_missing')
    denied("INSERT INTO notifications(recipient_role,recipient_id,kind,thread_id) VALUES('proctor',101,'synthetic',999)",'no_fk_orphan_notification','student_write_owner_missing')
    denied("INSERT INTO homework_chat_reads(thread_id,reader_role,reader_id,last_message_id) VALUES(2002,'admin',2,11)",'cross_thread_message_pointer','student_write_owner_conflict')
    denied("INSERT INTO homework_chat_messages(thread_id,sender_role,sender_id) VALUES(2002,'student',101)",'cross_thread_student_identity','student_write_owner_conflict')
    denied(outbox(90,'unknown.event',None,None,{}),'unknown_outbox_event','student_write_outbox_event_unknown')
    denied(outbox(90,'notification.created',None,999,{'room':'user:proctor:101','data':{'entity_id':999}}),'orphan_typed_notification','student_write_owner_missing')
    denied(outbox(90,'submission.changed',None,2002,{'room':'user:student:202','data':{'homework_id':1,'student_id':True,'submission_id':2002}}),'json_boolean_owner','student_write_json_id_invalid')
    denied(outbox(90,'notification.created',None,34,{'room':'user:Student:101','data':{'entity_id':34}}),'json_role_case_invalid','student_write_outbox_room_invalid')
    sql += [
        "INSERT INTO student_deletion_jobs(id,student_id,actor_role,actor_id,idempotency_key,confirmation_hash,fingerprint,student_json,manifest_json,total_units) VALUES('91111111-1111-4111-8111-111111111111',101,'admin',1,'81111111-1111-4111-8111-111111111111',REPEAT('1',64),REPEAT('2',64),JSON_OBJECT(),JSON_OBJECT('command_ids',JSON_ARRAY(1,3)),100)",
        "SET SESSION time_zone='+03:00'",
        "INSERT INTO student_deletion_barriers(student_id,job_id,created_at) VALUES(101,'91111111-1111-4111-8111-111111111111',UTC_TIMESTAMP(6))",
    ]
    assert_sql("ABS(TIMESTAMPDIFF(SECOND,(SELECT created_at FROM student_deletion_barriers WHERE student_id=101),UTC_TIMESTAMP(6)))<5",'barrier_explicit_utc')
    denied("INSERT INTO attendance(`date`,student_id) VALUES('2099-01-02',101)",'direct_insert_denied')
    denied("UPDATE homework_submissions SET current_file_id=NULL WHERE id=1001",'barred_student_pointer_clear_denied')
    denied("UPDATE attendance SET student_id=202 WHERE id=1",'old_owner_retarget_denied')
    denied("UPDATE attendance SET student_id=101 WHERE id=2",'new_owner_retarget_denied')
    denied("UPDATE classic_exam_attempt_part_progress SET consensus_count=1 WHERE attempt_id=1 AND source_part_id=1",'composite_pk_indirect_denied')
    denied("UPDATE homework_chat_reads SET last_message_id=0 WHERE thread_id=1001 AND reader_role='student' AND reader_id=101",'composite_pk_chat_read_denied')
    denied("UPDATE push_subscriptions SET enabled=0 WHERE id=31",'student_push_denied')
    denied("UPDATE notifications SET read_at=UTC_TIMESTAMP(6) WHERE id=31",'recipient_only_notification_denied')
    denied("UPDATE notifications SET read_at=UTC_TIMESTAMP(6) WHERE id=32",'staff_notification_about_student_denied')
    denied("UPDATE homework_chat_messages SET sender_role='proctor' WHERE id=11",'old_sender_change_denied')
    for i,event in enumerate(events,1):
        denied(outbox(i+100,*event),'typed_'+event[0].replace('.','_')+'_denied')
        denied(f'UPDATE homework_realtime_outbox SET expires_at=\'2099-02-01\' WHERE id={i}','stale_typed_'+event[0].replace('.','_')+'_denied')
    for key in ('student_id','studentId','studentID','existing_student_id','target_student_id','privilegeBaselineStudentId'):
        for value in (101,'101','000101'):
            document=json.dumps({'rows':[{'nested':{key:value}}]})
            denied("INSERT INTO user_import_sessions(preview_payload,expires_at) VALUES("+gen.literal(document)+",'2099-01-01')",'json_'+key+'_'+type(value).__name__+'_'+str(value))
    for key in ('student_ids','studentIds','student_ids_created'):
        for value in (101,'101','000101'):
            document=json.dumps({'rows':[{key:[202,value]}]})
            denied("INSERT INTO user_import_sessions(preview_payload,expires_at) VALUES("+gen.literal(document)+",'2099-01-01')",'json_array_'+key+'_'+type(value).__name__+'_'+str(value))
    denied("INSERT INTO user_import_sessions(preview_payload,expires_at) VALUES(JSON_OBJECT('student',JSON_OBJECT('id','000101')),'2099-01-01')",'json_nested_padded_id')
    sql.append("INSERT INTO user_import_sessions(preview_payload,expires_at) VALUES(JSON_OBJECT('actor',JSON_OBJECT('role','proctor','id',101)),'2099-01-01')")
    denied("UPDATE classic_exam_votes SET value=0 WHERE id=1",'classic_multihop_deleted_vote_denied')
    denied("UPDATE classic_exam_attempts SET current_presented_question_id=11 WHERE id=2",'classic_late_cross_question_denied')
    denied("UPDATE student_deletion_barriers SET job_id='01111111-1111-4111-8111-111111111111' WHERE student_id=101",'barrier_update_immutable','student_deletion_barrier_immutable')
    denied("DELETE FROM student_deletion_barriers WHERE student_id=101",'barrier_delete_immutable','student_deletion_barrier_immutable')
    denied("UPDATE exam_admin_commands SET target_student_id=NULL,target_attempt_id=NULL,receipt=NULL,tombstone=1,command_type='changed' WHERE id=1",'captured_command_changed_field_denied','student_command_tombstone_immutable')
    sql.append('UPDATE exam_admin_commands SET target_student_id=NULL,target_attempt_id=NULL,receipt=NULL,tombstone=1 WHERE id IN (1,3)')
    assert_sql('(SELECT COUNT(*) FROM exam_admin_commands WHERE id IN (1,3) AND target_student_id IS NULL AND target_attempt_id IS NULL AND receipt IS NULL AND tombstone=1)=2','authorized_command_sanitation')
    denied("DELETE FROM exam_admin_commands WHERE id=1",'command_tombstone_delete_denied','student_command_tombstone_immutable')
    denied("UPDATE exam_admin_commands SET receipt=JSON_OBJECT('student_id',101) WHERE id=1",'command_tombstone_replay_denied','student_command_tombstone_immutable')
    from actual_purge_sql_cases import add_purge_checks
    add_purge_checks(sql,checks,assert_sql,denied,gen)
    denied("UPDATE homework_file_jobs SET processed_output_key='synthetic/one.pdf' WHERE student_id=202",'late_adoption_after_unlink_ledger_denied')
    denied("UPDATE homework_submissions SET current_file_id=11 WHERE id=2002",'late_dangling_file_adoption_denied','student_write_owner_missing')
    sql += [
        "UPDATE push_subscriptions SET enabled=0 WHERE id=32",
        "UPDATE homework_chat_reads SET last_message_id=22 WHERE thread_id=2002 AND reader_role='proctor' AND reader_id=101",
        "UPDATE homework_realtime_presence SET is_typing=1 WHERE thread_id=2002 AND actor_role='proctor' AND actor_id=101",
        "UPDATE notifications SET read_at=UTC_TIMESTAMP(6) WHERE id=34",
        "UPDATE homework_realtime_outbox SET expires_at='2099-02-01' WHERE id=20",
        "UPDATE user_import_sessions SET preview_payload=JSON_OBJECT('student_id',202) WHERE id=1",
        "DELETE FROM homework_realtime_outbox WHERE id IN (1,2,3,4,5,6)",
        "DELETE FROM homework_realtime_presence WHERE thread_id=1001",
        "DELETE FROM notifications WHERE id IN (31,32)",
        "DELETE FROM push_subscriptions WHERE id=31",
        "DELETE FROM homework_chat_threads WHERE id=1001",
        "DELETE FROM homework_object_ownership WHERE student_id=101",
        "DELETE FROM homework_submissions WHERE student_id=101",
        "DELETE FROM classic_exam_attempts WHERE student_id=101",
        "DELETE FROM classic_exam_assignments WHERE student_id=101",
        "DELETE FROM auth_users WHERE role='student' AND ref_id=101",
        "DELETE FROM zaps WHERE student_id=101",
        "DELETE FROM students WHERE id=101",
    ]
    denied("INSERT INTO students(id,full_name,`class`,tg_name) VALUES(101,'Recreated',10,'synthetic')",'deleted_id_recreation_denied')
    denied("UPDATE homework_file_jobs SET processed_output_key='synthetic/one.pdf' WHERE student_id=202",'late_adoption_after_ledger_removed_denied')
    denied("INSERT INTO homework_submission_files(submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(2002,'synthetic/one.pdf','current',10,1,REPEAT('1',64))",'late_file_adoption_from_purge_receipt_denied')
    denied("INSERT INTO homework_object_ownership(object_key,student_id,submission_id,kind) VALUES('synthetic/one.pdf',202,2002,'processed')",'late_ledger_adoption_from_purge_receipt_denied')
    for table,where in [('students','id=101'),('attendance','student_id=101'),('student_credentials','student_id=101'),('homework_chat_messages','thread_id=1001'),('homework_chat_reads','thread_id=1001'),('homework_chat_admin_followers','thread_id=1001'),('classic_exam_attempt_part_progress','attempt_id=1'),('homework_submission_files','submission_id=1001')]:
        assert_sql(f'(SELECT COUNT(*) FROM {table} WHERE {where})=0','cascade_deleted_'+table)
    for table,where,count in [('students','id=202',1),('auth_users',"role='proctor' AND ref_id=101",1),('homework_chat_messages','thread_id=2002',2),('homework_chat_reads','thread_id=2002',2),('homework_realtime_presence','thread_id=2002',2),('notifications','id IN (33,34)',2),('push_subscriptions','id IN (32,33)',2),('homework_submissions','student_id=202',1),('homework_submission_files','submission_id=2002',1),('classic_exam_attempt_part_progress','attempt_id=2',1),('exams','id=1',1),('homework','id=1',1),('student_deletion_barriers','student_id=101',1)]:
        assert_sql(f'(SELECT COUNT(*) FROM {table} WHERE {where})={count}','preserved_'+table)
