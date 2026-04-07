CREATE TABLE bootstrap_admin_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  config_fingerprint TEXT NOT NULL,
  user_id TEXT NOT NULL,
  synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES user(id)
);
