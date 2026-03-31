PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  token_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires_at ON refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS asset_heads (
  asset_id TEXT PRIMARY KEY,
  asset_type TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  visibility TEXT NOT NULL,
  latest_revision TEXT NOT NULL,
  latest_version_number INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  schema_version TEXT,
  variant_kind TEXT,
  base_node_type TEXT,
  service_class TEXT,
  operator_class TEXT,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (owner_user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_heads_asset_type ON asset_heads(asset_type);
CREATE INDEX IF NOT EXISTS idx_asset_heads_owner_user_id ON asset_heads(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_asset_heads_visibility ON asset_heads(visibility);
CREATE INDEX IF NOT EXISTS idx_asset_heads_deleted_at ON asset_heads(deleted_at);
CREATE INDEX IF NOT EXISTS idx_asset_heads_base_node_type ON asset_heads(base_node_type);
CREATE INDEX IF NOT EXISTS idx_asset_heads_variant_kind ON asset_heads(variant_kind);
CREATE INDEX IF NOT EXISTS idx_asset_heads_schema_version ON asset_heads(schema_version);

CREATE TABLE IF NOT EXISTS asset_versions (
  asset_id TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  revision TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by_user_id TEXT NOT NULL,
  change_summary TEXT,
  PRIMARY KEY (asset_id, version_number),
  UNIQUE (asset_id, revision),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (created_by_user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_versions_asset_id ON asset_versions(asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_versions_created_at ON asset_versions(created_at);

CREATE TABLE IF NOT EXISTS asset_subscriptions (
  asset_id TEXT NOT NULL,
  subscriber_user_id TEXT NOT NULL,
  subscribed_at TEXT NOT NULL,
  last_seen_revision TEXT,
  PRIMARY KEY (asset_id, subscriber_user_id),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (subscriber_user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_subscriptions_subscriber_user_id ON asset_subscriptions(subscriber_user_id);
