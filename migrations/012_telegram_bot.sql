CREATE TABLE IF NOT EXISTS telegram_bot_settings (
    id TINYINT NOT NULL PRIMARY KEY,
    bot_token VARCHAR(255) NULL,
    autostart TINYINT(1) NOT NULL DEFAULT 0,
    welcome_text TEXT NULL,
    not_found_text TEXT NULL,
    credentials_text TEXT NULL,
    button_label VARCHAR(255) NOT NULL DEFAULT 'Узнать логин и пароль',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO telegram_bot_settings (
    id,
    welcome_text,
    not_found_text,
    credentials_text,
    button_label
)
VALUES (
    1,
    'Здравствуйте, {full_name}! Я помогу получить доступ к личному кабинету CPM.',
    'Ученик с таким Telegram не найден. Проверьте никнейм у администратора.',
    'Ваши данные для входа в CPM:\n\nЛогин: `{login}`\nПароль: `{password}`',
    'Узнать логин и пароль'
)
ON DUPLICATE KEY UPDATE id = id;

CREATE TABLE IF NOT EXISTS telegram_bot_chats (
    chat_id BIGINT NOT NULL PRIMARY KEY,
    student_id INT NULL,
    telegram_username VARCHAR(255) NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_credentials_sent_at TIMESTAMP NULL,
    messages_count INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    INDEX idx_telegram_bot_chats_student (student_id),
    CONSTRAINT fk_telegram_bot_chats_student
        FOREIGN KEY (student_id) REFERENCES students(id)
        ON DELETE SET NULL
);
