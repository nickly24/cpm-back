-- Werkzeug scrypt-хеши ~162 символа; varchar(50) обрезал их и ломал вход.
ALTER TABLE auth_users
    MODIFY COLUMN password VARCHAR(255) NULL;
