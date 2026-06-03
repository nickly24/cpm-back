CREATE TABLE IF NOT EXISTS zap_dates (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  zap_id INT NOT NULL COMMENT 'FK zaps.id (signed INT на prod)',
  date DATE NOT NULL,
  status ENUM('pending','linked','no_class_day','failed','cancelled') NOT NULL DEFAULT 'pending',
  class_day_id INT UNSIGNED NULL,
  error_code VARCHAR(50) NULL,
  error_message VARCHAR(255) NULL,
  linked_at DATETIME NULL,
  last_retry_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_zap_date (zap_id, date),
  CONSTRAINT fk_zd_zap FOREIGN KEY (zap_id) REFERENCES zaps(id) ON DELETE CASCADE,
  CONSTRAINT fk_zd_class_day FOREIGN KEY (class_day_id) REFERENCES class_days(id) ON DELETE SET NULL
);
CREATE INDEX idx_zap_dates_zap ON zap_dates(zap_id);
CREATE INDEX idx_zap_dates_status ON zap_dates(status);
