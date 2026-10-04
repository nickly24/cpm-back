"""Non-strict-mode migration preflight tests using synthetic pre-activation rows."""


def preparation_cases(gen):
    migration=gen.split_sql(gen.HOMEWORK/'migrations/002_student_purges.sql')
    proc=next(q for q in migration if q.startswith('CREATE PROCEDURE migrate_homework_purges_preflight('))
    sql=[
        "SET @cpm_saved_sql_mode=@@SESSION.sql_mode",
        "SET SESSION sql_mode=''",
        proc,
        "INSERT INTO students(id,full_name,`class`,tg_name) VALUES(909,'Synthetic preparation one',1,'synthetic'),(910,'Synthetic preparation two',1,'synthetic')",
        "INSERT INTO homework(id,name,type,deadline) VALUES(909,'Synthetic preparation',"+gen.literal('ОВ')+",'2099-01-01')",
        "INSERT INTO homework_submissions(id,homework_id,student_id) VALUES(909,909,909),(910,909,910)",
        "CREATE PROCEDURE cpm_test_prepare_assert(IN condition_value BOOLEAN,IN message_value VARCHAR(128)) BEGIN IF condition_value IS NULL OR NOT condition_value THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT=message_value; END IF; END",
    ]
    checks=[]
    def rejects(setup,label,expected,cleanup):
        name='cpm_test_prepare_'+str(len(checks)+1)
        sql.extend(setup)
        sql.append(f"CREATE PROCEDURE {name}() BEGIN DECLARE got TEXT DEFAULT NULL; BEGIN DECLARE CONTINUE HANDLER FOR SQLEXCEPTION BEGIN GET DIAGNOSTICS CONDITION 1 got=MESSAGE_TEXT; END; CALL migrate_homework_purges_preflight(); END; CALL cpm_test_prepare_assert(got='{expected}','{label}'); END")
        sql.append('CALL '+name+'()')
        sql.append('DROP PROCEDURE '+name)
        sql.append("CALL cpm_test_prepare_assert((SELECT COUNT(*) FROM homework_object_ownership)=0,'bad_key_preflight_never_backfilled')")
        sql.extend(cleanup);checks.append(label)
    for key,label in [('synthetic/é.pdf','non_ascii'),('','empty'),('   ','only_spaces')]:
        rejects(["INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(909,909,"+gen.literal(key)+",'current',1,1,REPEAT('1',64))"],
                'prepare_'+label+'_rejected_nonstrict','homework_historical_object_key_invalid',
                ["DELETE FROM homework_submission_files WHERE id=909"])
    sql += ["ALTER TABLE homework_file_jobs MODIFY COLUMN staging_key VARCHAR(1024) NULL",
            "INSERT INTO homework_file_jobs(id,client_upload_id,submission_id,homework_id,student_id) VALUES('90990990-9099-4099-8099-909909909909','80990990-9099-4099-8099-909909909909',909,909,909),('91091091-0910-4910-8910-910910910910','81091091-0910-4910-8910-910910910910',910,909,910)"]
    rejects(["UPDATE homework_file_jobs SET staging_key=REPEAT('a',513) WHERE student_id=909"],
            'prepare_oversized_key_rejected_nonstrict','homework_historical_object_key_invalid',
            ["UPDATE homework_file_jobs SET staging_key=NULL WHERE student_id=909"])
    rejects(["UPDATE homework_file_jobs SET staging_key='synthetic/shared' WHERE student_id IN (909,910)"],
            'prepare_shared_owner_key_rejected','homework_historical_object_key_collision',
            ["UPDATE homework_file_jobs SET staging_key=NULL WHERE student_id IN (909,910)"])
    rejects(["UPDATE homework_file_jobs SET staging_key=IF(student_id=909,'synthetic/alias','synthetic/alias ') WHERE student_id IN (909,910)"],
            'prepare_ascii_registry_alias_rejected','homework_historical_object_key_collision',
            ["UPDATE homework_file_jobs SET staging_key=NULL WHERE student_id IN (909,910)"])
    sql += ["ALTER TABLE homework_file_jobs MODIFY COLUMN staging_key VARCHAR(512) NULL",
            'DROP PROCEDURE migrate_homework_purges_preflight',
            "INSERT INTO homework_submission_files(id,submission_id,object_key,status,size_bytes,page_count,sha256) VALUES(909,909,'synthetic/Case.pdf','current',1,1,REPEAT('1',64))"]
    sql+=migration
    sql.append("UPDATE homework_file_jobs SET staging_key='synthetic/case.pdf' WHERE student_id=910")
    sql+=migration
    sql+=migration
    sql += [
        "CALL cpm_test_prepare_assert((SELECT COUNT(*) FROM homework_object_ownership WHERE BINARY object_key=BINARY 'synthetic/Case.pdf' AND student_id=909)=1,'prepare_preserves_uppercase_key_owner')",
        "CALL cpm_test_prepare_assert((SELECT COUNT(*) FROM homework_object_ownership WHERE BINARY object_key=BINARY 'synthetic/case.pdf' AND student_id=910)=1,'prepare_preserves_lowercase_key_owner')",
        "CALL cpm_test_prepare_assert((SELECT COUNT(*) FROM homework_object_ownership)=2,'prepare_case_distinct_repeatable')",
        "DELETE FROM homework_object_ownership WHERE student_id IN (909,910)",
        "DELETE FROM homework_file_jobs WHERE student_id IN (909,910)",
        "DELETE FROM homework_submission_files WHERE id=909",
        "DELETE FROM homework_submissions WHERE student_id IN (909,910)",
        "DELETE FROM homework WHERE id=909",
        "DELETE FROM students WHERE id IN (909,910)",
        'DROP PROCEDURE cpm_test_prepare_assert',
        'SET SESSION sql_mode=@cpm_saved_sql_mode',
    ]
    checks += ['prepare_case_distinct_key_owners_preserved','prepare_repeatable_with_existing_content']
    return sql,checks
