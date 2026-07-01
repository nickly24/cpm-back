CREATE TABLE IF NOT EXISTS student_credentials (
    student_id INT NOT NULL PRIMARY KEY,
    login VARCHAR(255) NOT NULL,
    password VARCHAR(255) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_student_credentials_student
        FOREIGN KEY (student_id) REFERENCES students(id)
        ON DELETE CASCADE
);
