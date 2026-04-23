PRAGMA foreign_keys = ON;

CREATE TABLE user (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE,
  emailVerified INTEGER NOT NULL DEFAULT 0,
  image TEXT,
  createdAt INTEGER NOT NULL,
  updatedAt INTEGER NOT NULL,
  role TEXT,
  banned INTEGER NOT NULL DEFAULT 0,
  banReason TEXT,
  banExpires INTEGER
);

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

CREATE TABLE rateLimit (
  key TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  lastRequest INTEGER NOT NULL
);

CREATE TABLE asset_heads (
  asset_id TEXT PRIMARY KEY,
  asset_type TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  visibility TEXT NOT NULL,
  current_version_number INTEGER NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (owner_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_heads_type_visibility_name
  ON asset_heads(asset_type, visibility, LOWER(name), asset_id);
CREATE INDEX idx_asset_heads_type_owner_name
  ON asset_heads(asset_type, owner_user_id, LOWER(name), asset_id);
CREATE INDEX idx_asset_heads_type_updated
  ON asset_heads(asset_type, updated_at DESC, asset_id);
CREATE INDEX idx_asset_heads_updated
  ON asset_heads(updated_at DESC, asset_id);
CREATE INDEX idx_asset_heads_owner_updated
  ON asset_heads(owner_user_id, updated_at DESC, asset_id);

CREATE TABLE variant_details (
  asset_id TEXT PRIMARY KEY,
  variant_kind TEXT NOT NULL,
  base_node_type TEXT NOT NULL,
  service_class TEXT NOT NULL,
  operator_class TEXT,
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id) ON DELETE CASCADE
);

CREATE INDEX idx_variant_details_base_node_type ON variant_details(base_node_type);
CREATE INDEX idx_variant_details_variant_kind ON variant_details(variant_kind);
CREATE INDEX idx_variant_details_service_class ON variant_details(service_class);
CREATE INDEX idx_variant_details_lookup ON variant_details(base_node_type, variant_kind, service_class);

CREATE TABLE asset_versions (
  asset_id TEXT NOT NULL,
  version_number INTEGER NOT NULL,
  content BLOB NOT NULL,
  created_at TEXT NOT NULL,
  created_by_user_id TEXT NOT NULL,
  change_summary TEXT,
  PRIMARY KEY (asset_id, version_number),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (created_by_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_versions_asset_id ON asset_versions(asset_id);
CREATE INDEX idx_asset_versions_created_at ON asset_versions(created_at);

CREATE TABLE asset_subscriptions (
  asset_id TEXT NOT NULL,
  subscriber_user_id TEXT NOT NULL,
  subscribed_at TEXT NOT NULL,
  last_seen_version_number INTEGER,
  PRIMARY KEY (asset_id, subscriber_user_id),
  FOREIGN KEY (asset_id) REFERENCES asset_heads(asset_id),
  FOREIGN KEY (subscriber_user_id) REFERENCES user(id)
);

CREATE INDEX idx_asset_subscriptions_subscriber_user_id ON asset_subscriptions(subscriber_user_id);

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

CREATE TABLE bootstrap_admin_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  config_fingerprint TEXT NOT NULL,
  user_id TEXT NOT NULL,
  synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES user(id)
);

CREATE TABLE desktop_authorization_codes (
  code TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  code_challenge TEXT NOT NULL,
  code_challenge_method TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);

CREATE INDEX idx_desktop_authorization_codes_expires_at
  ON desktop_authorization_codes(expires_at);
CREATE INDEX idx_desktop_authorization_codes_user_id
  ON desktop_authorization_codes(user_id);

CREATE TABLE desktop_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  access_token_hash TEXT NOT NULL UNIQUE,
  access_token_expires_at INTEGER NOT NULL,
  refresh_token_hash TEXT NOT NULL UNIQUE,
  refresh_token_expires_at INTEGER NOT NULL,
  revoked_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
);

CREATE INDEX idx_desktop_sessions_user_id
  ON desktop_sessions(user_id);
CREATE INDEX idx_desktop_sessions_access_token_expires_at
  ON desktop_sessions(access_token_expires_at);
CREATE INDEX idx_desktop_sessions_refresh_token_expires_at
  ON desktop_sessions(refresh_token_expires_at);
