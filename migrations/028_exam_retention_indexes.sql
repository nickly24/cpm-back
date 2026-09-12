-- Additive bounded-retention access paths; existing values are unchanged.

-- @step {"id":"outside_import_expiry_index","kind":"indexes","table":"outside_exam_result_import_sessions","indexes":["ix_outside_import_expiry"],"requires":[]}
ALTER TABLE outside_exam_result_import_sessions ADD KEY ix_outside_import_expiry(expires_at,id,exam_id);

-- @step {"id":"question_import_expiry_index","kind":"indexes","table":"classic_exam_question_import_sessions","indexes":["ix_question_import_expiry"],"requires":[]}
ALTER TABLE classic_exam_question_import_sessions ADD KEY ix_question_import_expiry(expires_at,id,exam_id);

-- @step {"id":"assignment_import_expiry_index","kind":"indexes","table":"classic_exam_assignment_import_sessions","indexes":["ix_assignment_import_expiry"],"requires":[]}
ALTER TABLE classic_exam_assignment_import_sessions ADD KEY ix_assignment_import_expiry(expires_at,id,exam_id);

-- @step {"id":"admin_command_expiry_index","kind":"indexes","table":"exam_admin_commands","indexes":["ix_admin_command_expiry"],"requires":[]}
ALTER TABLE exam_admin_commands ADD KEY ix_admin_command_expiry(expires_at,id);
