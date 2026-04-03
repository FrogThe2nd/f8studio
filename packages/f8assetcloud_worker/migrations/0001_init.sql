PRAGMA foreign_keys = ON;

CREATE TABLE user (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  emailVerified INTEGER NOT NULL DEFAULT 0,
  image TEXT,
  createdAt INTEGER NOT NULL,
  updatedAt INTEGER NOT NULL,
  username TEXT UNIQUE,
  displayUsername TEXT,
  role TEXT,
  banned INTEGER NOT NULL DEFAULT 0,
  banReason TEXT,
  banExpires INTEGER
);

CREATE INDEX idx_user_username ON user(username);
CREATE INDEX idx_user_role ON user(role);

CREATE TABLE session (
  id TEXT PRIMARY KEY,
  expiresAt INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  createdAt INTEGER NOT NULL,
  updatedAt INTEGER NOT NULL,
  ipAddress TEXT,
  userAgent TEXT,
  userId TEXT NOT NULL,
  impersonatedBy TEXT,
  FOREIGN KEY (userId) REFERENCES user(id) ON DELETE CASCADE
);

CREATE INDEX idx_session_userId ON session(userId);
CREATE INDEX idx_session_expiresAt ON session(expiresAt);

CREATE TABLE account (
  id TEXT PRIMARY KEY,
  accountId TEXT NOT NULL,
  providerId TEXT NOT NULL,
  userId TEXT NOT NULL,
  accessToken TEXT,
  refreshToken TEXT,
  idToken TEXT,
  accessTokenExpiresAt INTEGER,
  refreshTokenExpiresAt INTEGER,
  scope TEXT,
  password TEXT,
  createdAt INTEGER NOT NULL,
  updatedAt INTEGER NOT NULL,
  FOREIGN KEY (userId) REFERENCES user(id) ON DELETE CASCADE,
  UNIQUE (providerId, accountId)
);

CREATE INDEX idx_account_userId ON account(userId);

CREATE TABLE verification (
  id TEXT PRIMARY KEY,
  identifier TEXT NOT NULL,
  value TEXT NOT NULL,
  expiresAt INTEGER NOT NULL,
  createdAt INTEGER NOT NULL,
  updatedAt INTEGER NOT NULL
);

CREATE INDEX idx_verification_identifier ON verification(identifier);
CREATE INDEX idx_verification_expiresAt ON verification(expiresAt);

CREATE TABLE asset_heads (
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
  FOREIGN KEY (owner_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_heads_asset_type ON asset_heads(asset_type);
CREATE INDEX idx_asset_heads_owner_user_id ON asset_heads(owner_user_id);
CREATE INDEX idx_asset_heads_visibility ON asset_heads(visibility);
CREATE INDEX idx_asset_heads_deleted_at ON asset_heads(deleted_at);
CREATE INDEX idx_asset_heads_base_node_type ON asset_heads(base_node_type);
CREATE INDEX idx_asset_heads_variant_kind ON asset_heads(variant_kind);
CREATE INDEX idx_asset_heads_schema_version ON asset_heads(schema_version);

CREATE TABLE asset_versions (
  asset_id TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  revision TEXT NOT NULL,
  content BLOB NOT NULL,
  created_at TEXT NOT NULL,
  created_by_user_id TEXT NOT NULL,
  change_summary TEXT,
  PRIMARY KEY (asset_id, version_number),
  UNIQUE (asset_id, revision),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (created_by_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_versions_asset_id ON asset_versions(asset_id);
CREATE INDEX idx_asset_versions_created_at ON asset_versions(created_at);

CREATE TABLE asset_subscriptions (
  asset_id TEXT NOT NULL,
  subscriber_user_id TEXT NOT NULL,
  subscribed_at TEXT NOT NULL,
  last_seen_revision TEXT,
  PRIMARY KEY (asset_id, subscriber_user_id),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (subscriber_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_subscriptions_subscriber_user_id ON asset_subscriptions(subscriber_user_id);
