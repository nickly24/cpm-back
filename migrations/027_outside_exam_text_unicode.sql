-- Compatibility hardening only; deploy compatible backend before applying.
-- Preserve legacy table charset and values; only the user-entered free-text
-- examiner column needs full Unicode (including four-byte UTF-8 characters).

-- @step {"id":"outside_examiner_full_unicode","kind":"modify_columns","table":"exam_sessions","columns":{"examinator":{"type":"varchar(255)","nullable":false,"charset":"utf8mb4","collation":"utf8mb4_unicode_ci"}},"before":{"examinator":{"type":"varchar(255)","nullable":false,"charset":"utf8mb3"}},"requires":[]}
ALTER TABLE exam_sessions MODIFY COLUMN examinator VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL;
