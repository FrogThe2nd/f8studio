CREATE TABLE site_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  allow_user_registration INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by_user_id TEXT,
  FOREIGN KEY (updated_by_user_id) REFERENCES user(id)
);

INSERT INTO site_settings (id, allow_user_registration, updated_at, updated_by_user_id)
VALUES (1, 0, CURRENT_TIMESTAMP, NULL)
ON CONFLICT(id) DO NOTHING;
