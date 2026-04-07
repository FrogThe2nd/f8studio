DROP INDEX IF EXISTS idx_asset_heads_asset_type;
DROP INDEX IF EXISTS idx_asset_heads_owner_user_id;
DROP INDEX IF EXISTS idx_asset_heads_visibility;
DROP INDEX IF EXISTS idx_asset_heads_deleted_at;

CREATE INDEX idx_asset_heads_type_visibility_name
  ON asset_heads(asset_type, visibility, deleted_at, LOWER(name), asset_id);

CREATE INDEX idx_asset_heads_type_owner_name
  ON asset_heads(asset_type, owner_user_id, deleted_at, LOWER(name), asset_id);

CREATE INDEX idx_asset_heads_type_deleted_updated
  ON asset_heads(asset_type, deleted_at, updated_at DESC, asset_id);

CREATE INDEX idx_asset_heads_deleted_updated
  ON asset_heads(deleted_at, updated_at DESC, asset_id);

CREATE INDEX idx_asset_heads_owner_deleted_updated
  ON asset_heads(owner_user_id, deleted_at, updated_at DESC, asset_id);
