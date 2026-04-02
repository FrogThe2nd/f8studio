import { nowIso } from './auth.js';

const PAGE_SIZE = 100;
const COMPONENT_SCHEMA_VERSION = 'f8studio-session/1';

export class AssetConflictError extends Error {
  constructor({ assetId, revision }) {
    super(`Asset ${assetId} update conflict`);
    this.assetId = String(assetId);
    this.revision = String(revision);
  }
}

export class AssetPermissionError extends Error {}
export class AssetNotFoundError extends Error {}

export class AssetRepository {
  constructor(db) {
    this._db = db;
  }

  async listUsers({ query, cursor }) {
    const filters = ['1 = 1'];
    const bindings = [];
    if (String(query || '').trim()) {
      const match = `%${String(query).trim().toLowerCase()}%`;
      filters.push('(LOWER(COALESCE(u.username, \'\')) LIKE ? OR LOWER(COALESCE(u.displayUsername, u.name, \'\')) LIKE ? OR LOWER(u.email) LIKE ?)');
      bindings.push(match, match, match);
    }
    const start = parseCursor(cursor);
    const sql = `
      SELECT
        u.id,
        u.username,
        u.displayUsername,
        u.name,
        u.email,
        u.emailVerified,
        u.role,
        u.createdAt,
        u.updatedAt,
        COALESCE(assets.asset_count, 0) AS asset_count
      FROM user u
      LEFT JOIN (
        SELECT owner_user_id, COUNT(*) AS asset_count
        FROM asset_heads
        WHERE deleted_at IS NULL
        GROUP BY owner_user_id
      ) assets ON assets.owner_user_id = u.id
      WHERE ${filters.join(' AND ')}
      ORDER BY LOWER(COALESCE(u.username, u.email)), u.id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => ({
        userId: String(row.id),
        username: stringOrDefault(row.username, String(row.email || '')),
        displayName: stringOrDefault(row.displayUsername, stringOrDefault(row.name, String(row.email || ''))),
        email: String(row.email || ''),
        emailVerified: Number(row.emailVerified || 0) !== 0,
        isAdmin: String(row.role || '') === 'admin',
        assetCount: Number(row.asset_count || 0),
        createdAt: normalizeDbTimestamp(row.createdAt),
        updatedAt: normalizeDbTimestamp(row.updatedAt),
      })),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async getUserByIdWithStats(userId) {
    const row = await this._db.prepare(
      `SELECT
         u.id,
         u.username,
         u.displayUsername,
         u.name,
         u.email,
         u.emailVerified,
         u.role,
         u.createdAt,
         u.updatedAt,
         COALESCE(assets.asset_count, 0) AS asset_count
       FROM user u
       LEFT JOIN (
         SELECT owner_user_id, COUNT(*) AS asset_count
         FROM asset_heads
         WHERE deleted_at IS NULL
         GROUP BY owner_user_id
       ) assets ON assets.owner_user_id = u.id
       WHERE u.id = ?`,
    )
      .bind(String(userId))
      .first();
    if (row === null) {
      return null;
    }
    return {
      userId: String(row.id),
      username: stringOrDefault(row.username, String(row.email || '')),
      displayName: stringOrDefault(row.displayUsername, stringOrDefault(row.name, String(row.email || ''))),
      email: String(row.email || ''),
      emailVerified: Number(row.emailVerified || 0) !== 0,
      isAdmin: String(row.role || '') === 'admin',
      assetCount: Number(row.asset_count || 0),
      createdAt: normalizeDbTimestamp(row.createdAt),
      updatedAt: normalizeDbTimestamp(row.updatedAt),
    };
  }

  async listManagedAssets({ assetType, ownerUserId, query, includeDeleted, cursor }) {
    const filters = ['1 = 1'];
    const bindings = [];
    const normalizedAssetType = String(assetType || '').trim();
    if (normalizedAssetType) {
      if (normalizedAssetType !== 'variant' && normalizedAssetType !== 'component') {
        throw new Error('assetType must be variant or component');
      }
      filters.push('h.asset_type = ?');
      bindings.push(normalizedAssetType);
    }
    const ownerFilter = String(ownerUserId || '').trim();
    if (ownerFilter) {
      filters.push('h.owner_user_id = ?');
      bindings.push(ownerFilter);
    }
    if (!toBoolean(includeDeleted)) {
      filters.push('h.deleted_at IS NULL');
    }
    if (String(query || '').trim()) {
      const match = `%${String(query).trim().toLowerCase()}%`;
      filters.push('(LOWER(h.name) LIKE ? OR LOWER(h.description) LIKE ? OR LOWER(h.tags_json) LIKE ?)');
      bindings.push(match, match, match);
    }
    const start = parseCursor(cursor);
    const sql = `
      SELECT
        h.asset_id,
        h.asset_type,
        h.owner_user_id,
        h.visibility,
        h.latest_revision,
        h.latest_version_number,
        h.name,
        h.description,
        h.tags_json,
        h.schema_version,
        h.variant_kind,
        h.base_node_type,
        h.service_class,
        h.operator_class,
        h.deleted_at,
        h.created_at,
        h.updated_at,
        COALESCE(u.displayUsername, u.name) AS owner_display_name,
        v.content_json,
        v.created_at AS version_created_at,
        v.created_by_user_id,
        v.change_summary,
        v.version_number,
        v.revision
      FROM asset_heads h
      JOIN asset_versions v
        ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
      LEFT JOIN user u ON u.id = h.owner_user_id
      WHERE ${filters.join(' AND ')}
      ORDER BY h.updated_at DESC, h.asset_id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => adminAssetPayloadFromRow(row)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async listAssetsByOwnerForManagement({ ownerUserId, assetType, includeDeleted, cursor }) {
    return this.listManagedAssets({
      ownerUserId,
      assetType,
      includeDeleted,
      query: '',
      cursor,
    });
  }

  async getManagedAsset({ assetId, includeDeleted }) {
    const head = await this._findAssetHeadRow(assetId, { includeDeleted: toBoolean(includeDeleted) });
    if (head === null) {
      return null;
    }
    const version = await this._findAssetVersionRow(assetId, Number(head.latest_version_number));
    if (version === null) {
      return null;
    }
    return adminAssetPayloadFromRow({ ...head, ...version });
  }

  async adminDeleteAsset({ assetId }) {
    const existing = await this._findAssetHeadRow(assetId, { includeDeleted: true });
    if (existing === null) {
      return false;
    }
    await this._db.prepare(
      `UPDATE asset_heads
       SET deleted_at = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(nowIso(), nowIso(), String(assetId))
      .run();
    return true;
  }

  async adminRestoreAsset({ assetId }) {
    const existing = await this._findAssetHeadRow(assetId, { includeDeleted: true });
    if (existing === null) {
      return false;
    }
    await this._db.prepare(
      `UPDATE asset_heads
       SET deleted_at = NULL,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(nowIso(), String(assetId))
      .run();
    return true;
  }

  async adminUpdateAssetVisibility({ assetId, visibility }) {
    const existing = await this._findAssetHeadRow(assetId, { includeDeleted: true });
    if (existing === null) {
      return null;
    }
    await this._db.prepare(
      `UPDATE asset_heads
       SET visibility = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(normalizeVisibility(visibility), nowIso(), String(assetId))
      .run();
    return this.getManagedAsset({ assetId, includeDeleted: true });
  }

  async createVariant({ payload, user }) {
    const normalized = normalizeVariantCreatePayload(payload, user);
    return this._createAsset({ normalized, userId: user.userId });
  }

  async updateVariant({ variantId, payload, user }) {
    const existing = await this._requireOwnedAsset({ assetId: variantId, assetType: 'variant', userId: user.userId });
    const normalized = normalizeVariantUpdatePayload(payload, existing, user);
    return this._updateAsset({ existing, normalized, userId: user.userId });
  }

  async deleteVariant({ variantId, userId }) {
    await this._deleteOwnedAsset({ assetId: variantId, assetType: 'variant', userId });
  }

  async getVariant({ variantId, userId }) {
    return this._getAssetPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber: null });
  }

  async listVariants({ userId, kind, baseNodeType, query, visibility, owner, cursor }) {
    return this._listAssets({
      assetType: 'variant',
      userId,
      query,
      cursor,
      visibility,
      owner,
      extraFilters: {
        variantKind: kind,
        baseNodeType,
      },
    });
  }

  async listVariantVersions({ variantId, userId }) {
    return this._listAssetVersions({ assetId: variantId, assetType: 'variant', userId });
  }

  async getVariantVersion({ variantId, versionNumber, userId }) {
    return this._getAssetPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber });
  }

  async subscribeVariant({ variantId, userId }) {
    return this._subscribeAsset({ assetId: variantId, assetType: 'variant', userId });
  }

  async unsubscribeVariant({ variantId, userId }) {
    return this._unsubscribeAsset({ assetId: variantId, assetType: 'variant', userId });
  }

  async forkVariant({ variantId, payload, user }) {
    return this._forkAsset({ assetId: variantId, assetType: 'variant', payload, user });
  }

  async createComponent({ payload, user }) {
    const normalized = normalizeComponentCreatePayload(payload, user);
    return this._createAsset({ normalized, userId: user.userId });
  }

  async updateComponent({ componentId, payload, user }) {
    const existing = await this._requireOwnedAsset({ assetId: componentId, assetType: 'component', userId: user.userId });
    const normalized = normalizeComponentUpdatePayload(payload, existing, user);
    return this._updateAsset({ existing, normalized, userId: user.userId });
  }

  async deleteComponent({ componentId, userId }) {
    await this._deleteOwnedAsset({ assetId: componentId, assetType: 'component', userId });
  }

  async getComponent({ componentId, userId }) {
    return this._getAssetPayload({ assetId: componentId, assetType: 'component', userId, versionNumber: null });
  }

  async listComponents({ userId, query, visibility, owner, cursor }) {
    return this._listAssets({
      assetType: 'component',
      userId,
      query,
      cursor,
      visibility,
      owner,
      extraFilters: {},
    });
  }

  async listComponentVersions({ componentId, userId }) {
    return this._listAssetVersions({ assetId: componentId, assetType: 'component', userId });
  }

  async getComponentVersion({ componentId, versionNumber, userId }) {
    return this._getAssetPayload({ assetId: componentId, assetType: 'component', userId, versionNumber });
  }

  async subscribeComponent({ componentId, userId }) {
    return this._subscribeAsset({ assetId: componentId, assetType: 'component', userId });
  }

  async unsubscribeComponent({ componentId, userId }) {
    return this._unsubscribeAsset({ assetId: componentId, assetType: 'component', userId });
  }

  async forkComponent({ componentId, payload, user }) {
    return this._forkAsset({ assetId: componentId, assetType: 'component', payload, user });
  }

  async searchAssets({ assetType, userId, query, visibility, owner, cursor }) {
    return this._listAssets({
      assetType: normalizeAssetType(assetType),
      userId,
      query,
      cursor,
      visibility,
      owner,
      extraFilters: {},
    });
  }

  async _createAsset({ normalized, userId }) {
    const existing = await this._findAssetHeadRow(normalized.assetId, { includeDeleted: false });
    if (existing !== null) {
      throw new Error('assetId already exists');
    }
    await this._db.prepare(
      `INSERT INTO asset_heads (
         asset_id, asset_type, owner_user_id, visibility, latest_revision, latest_version_number,
         name, description, tags_json, schema_version, variant_kind, base_node_type,
         service_class, operator_class, deleted_at, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)`,
    )
      .bind(
        normalized.assetId,
        normalized.assetType,
        normalized.ownerUserId,
        normalized.visibility,
        normalized.revision,
        1,
        normalized.name,
        normalized.description,
        JSON.stringify(normalized.tags),
        normalized.schemaVersion,
        normalized.variantKind,
        normalized.baseNodeType,
        normalized.serviceClass,
        normalized.operatorClass,
        normalized.createdAt,
        normalized.updatedAt,
      )
      .run();
    await this._insertAssetVersion({
      assetId: normalized.assetId,
      versionNumber: 1,
      revision: normalized.revision,
      contentJson: normalized.contentJson,
      createdAt: normalized.updatedAt,
      createdByUserId: userId,
      changeSummary: normalized.changeSummary,
    });
    return this._getAssetPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _updateAsset({ existing, normalized, userId }) {
    if (String(normalized.revision || '') !== String(existing.latest_revision || '')) {
      throw new AssetConflictError({ assetId: existing.asset_id, revision: String(existing.latest_revision) });
    }
    const nextVersionNumber = Number(existing.latest_version_number) + 1;
    const nextRevision = nextRevisionForAsset(String(existing.latest_revision));
    await this._db.prepare(
      `UPDATE asset_heads
       SET visibility = ?,
           latest_revision = ?,
           latest_version_number = ?,
           name = ?,
           description = ?,
           tags_json = ?,
           schema_version = ?,
           variant_kind = ?,
           base_node_type = ?,
           service_class = ?,
           operator_class = ?,
           deleted_at = NULL,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(
        normalized.visibility,
        nextRevision,
        nextVersionNumber,
        normalized.name,
        normalized.description,
        JSON.stringify(normalized.tags),
        normalized.schemaVersion,
        normalized.variantKind,
        normalized.baseNodeType,
        normalized.serviceClass,
        normalized.operatorClass,
        normalized.updatedAt,
        normalized.assetId,
      )
      .run();
    await this._insertAssetVersion({
      assetId: normalized.assetId,
      versionNumber: nextVersionNumber,
      revision: nextRevision,
      contentJson: normalized.contentJson,
      createdAt: normalized.updatedAt,
      createdByUserId: userId,
      changeSummary: normalized.changeSummary,
    });
    await this._db.prepare(
      `UPDATE asset_subscriptions SET last_seen_revision = COALESCE(last_seen_revision, ?) WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(nextRevision, normalized.assetId, userId)
      .run();
    return this._getAssetPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _deleteOwnedAsset({ assetId, assetType, userId }) {
    await this._requireOwnedAsset({ assetId, assetType, userId });
    await this._db.prepare(
      `UPDATE asset_heads SET deleted_at = ?, updated_at = ? WHERE asset_id = ?`,
    )
      .bind(nowIso(), nowIso(), String(assetId))
      .run();
  }

  async _getAssetPayload({ assetId, assetType, userId, versionNumber }) {
    const head = await this._findAssetHeadRow(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);
    const targetVersionNumber = versionNumber === null ? Number(head.latest_version_number) : normalizeVersionNumber(versionNumber);
    if (targetVersionNumber !== Number(head.latest_version_number) && String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId || '')) {
      throw new AssetPermissionError('forbidden');
    }
    const version = await this._findAssetVersionRow(assetId, targetVersionNumber);
    if (version === null) {
      throw new AssetNotFoundError(`Asset version ${assetId}:${targetVersionNumber} not found`);
    }
    const subscription = userId ? await this._findSubscriptionRow(assetId, userId) : null;
    return assetPayloadFromRows({ head, version, subscription, viewerUserId: userId });
  }

  async _listAssets({ assetType, userId, query, cursor, visibility, owner, extraFilters }) {
    const filters = ['h.deleted_at IS NULL', 'h.asset_type = ?'];
    const bindings = [assetType];
    applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner });
    if (query) {
      const match = `%${String(query).trim().toLowerCase()}%`;
      filters.push('(LOWER(h.name) LIKE ? OR LOWER(h.description) LIKE ? OR LOWER(h.tags_json) LIKE ? OR LOWER(COALESCE(h.base_node_type, \'\')) LIKE ?)');
      bindings.push(match, match, match, match);
    }
    if (assetType === 'variant') {
      if (extraFilters.variantKind) {
        filters.push('h.variant_kind = ?');
        bindings.push(String(extraFilters.variantKind));
      }
      if (extraFilters.baseNodeType) {
        filters.push('h.base_node_type = ?');
        bindings.push(String(extraFilters.baseNodeType));
      }
    }
    const start = parseCursor(cursor);
    bindings.push(PAGE_SIZE + 1, start);
    const sql = `
      SELECT
        h.asset_id,
        h.asset_type,
        h.owner_user_id,
        h.visibility,
        h.latest_revision,
        h.latest_version_number,
        h.name,
        h.description,
        h.tags_json,
        h.schema_version,
        h.variant_kind,
        h.base_node_type,
        h.service_class,
        h.operator_class,
        h.deleted_at,
        h.created_at,
        h.updated_at,
        COALESCE(u.displayUsername, u.name) AS owner_display_name,
        s.subscribed_at,
        s.last_seen_revision,
        v.content_json,
        v.created_at AS version_created_at,
        v.created_by_user_id,
        v.change_summary,
        v.version_number,
        v.revision
      FROM asset_heads h
      JOIN asset_versions v
        ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
      LEFT JOIN user u ON u.id = h.owner_user_id
      LEFT JOIN asset_subscriptions s ON s.asset_id = h.asset_id AND s.subscriber_user_id = ?
      WHERE ${filters.join(' AND ')}
      ORDER BY LOWER(h.name), h.asset_id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(userId ? String(userId) : '', ...bindings).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => assetPayloadFromRows({ head: row, version: row, subscription: row, viewerUserId: userId })),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async _listAssetVersions({ assetId, assetType, userId }) {
    const head = await this._findAssetHeadRow(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);
    if (String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId || '')) {
      throw new AssetPermissionError('forbidden');
    }
    const result = await this._db.prepare(
      `SELECT asset_id, version_number, revision, content_json, created_at, created_by_user_id, change_summary
       FROM asset_versions WHERE asset_id = ? ORDER BY version_number DESC`,
    )
      .bind(String(assetId))
      .all();
    const rows = Array.isArray(result.results) ? result.results : [];
    return {
      versions: rows.map((row) => ({
        assetId: String(row.asset_id),
        assetType,
        versionNumber: Number(row.version_number),
        revision: String(row.revision),
        createdAt: String(row.created_at),
        createdByUserId: String(row.created_by_user_id),
        changeSummary: row.change_summary === null ? null : String(row.change_summary),
      })),
    };
  }

  async _subscribeAsset({ assetId, assetType, userId }) {
    const head = await this._findAssetHeadRow(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    const now = nowIso();
    await this._db.prepare(
      `INSERT INTO asset_subscriptions (asset_id, subscriber_user_id, subscribed_at, last_seen_revision)
       VALUES (?, ?, ?, ?)
       ON CONFLICT(asset_id, subscriber_user_id)
       DO UPDATE SET last_seen_revision = excluded.last_seen_revision`,
    )
      .bind(String(assetId), String(userId), now, String(head.latest_revision))
      .run();
    return this._getAssetPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _unsubscribeAsset({ assetId, assetType, userId }) {
    const head = await this._findAssetHeadRow(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    await this._db.prepare(
      `DELETE FROM asset_subscriptions WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(String(assetId), String(userId))
      .run();
    return {};
  }

  async _forkAsset({ assetId, assetType, payload, user }) {
    const source = await this._getAssetPayload({ assetId, assetType, userId: user.userId, versionNumber: null });
    const forkPayload = isPlainObject(payload) ? payload : {};
    if (assetType === 'variant') {
      const sourceRecord = source.record;
      const nextVariantId = stringOrDefault(forkPayload.variantId, crypto.randomUUID());
      const record = {
        ...sourceRecord,
        variantId: nextVariantId,
        name: stringOrDefault(forkPayload.name, `${sourceRecord.name} Copy`),
      };
      return this.createVariant({
        payload: {
          record,
          visibility: stringOrDefault(forkPayload.visibility, 'private'),
          changeSummary: stringOrDefault(forkPayload.changeSummary, `Forked from ${assetId}`),
        },
        user,
      });
    }
    const sourceRecord = source.record;
    const nextComponentId = stringOrDefault(forkPayload.componentId, crypto.randomUUID());
    const content = deepCloneJson(sourceRecord.content);
    return this.createComponent({
      payload: {
        record: {
          ...sourceRecord,
          componentId: nextComponentId,
          name: stringOrDefault(forkPayload.name, `${sourceRecord.name} Copy`),
          content,
        },
        visibility: stringOrDefault(forkPayload.visibility, 'private'),
        changeSummary: stringOrDefault(forkPayload.changeSummary, `Forked from ${assetId}`),
      },
      user,
    });
  }

  async _requireOwnedAsset({ assetId, assetType, userId }) {
    const existing = await this._findAssetHeadRow(assetId);
    if (existing === null || String(existing.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(existing.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    return existing;
  }

  async _insertAssetVersion({ assetId, versionNumber, revision, contentJson, createdAt, createdByUserId, changeSummary }) {
    await this._db.prepare(
      `INSERT INTO asset_versions (
         asset_id, version_number, revision, content_json, created_at, created_by_user_id, change_summary
       ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        String(assetId),
        Number(versionNumber),
        String(revision),
        String(contentJson),
        String(createdAt),
        String(createdByUserId),
        nullableString(changeSummary),
      )
      .run();
  }

  async _findAssetHeadRow(assetId, { includeDeleted = false } = {}) {
    const deletedFilter = includeDeleted ? '' : 'AND h.deleted_at IS NULL';
    const row = await this._db.prepare(
      `SELECT
         h.asset_id,
         h.asset_type,
         h.owner_user_id,
         h.visibility,
         h.latest_revision,
         h.latest_version_number,
         h.name,
         h.description,
         h.tags_json,
         h.schema_version,
         h.variant_kind,
         h.base_node_type,
         h.service_class,
         h.operator_class,
         h.deleted_at,
         h.created_at,
         h.updated_at,
         COALESCE(u.displayUsername, u.name) AS owner_display_name
       FROM asset_heads h
       LEFT JOIN user u ON u.id = h.owner_user_id
       WHERE h.asset_id = ? ${deletedFilter}`,
    )
      .bind(String(assetId))
      .first();
    return row === null ? null : row;
  }

  async _findAssetVersionRow(assetId, versionNumber) {
    const row = await this._db.prepare(
      `SELECT asset_id, version_number, revision, content_json, created_at, created_by_user_id, change_summary
       FROM asset_versions WHERE asset_id = ? AND version_number = ?`,
    )
      .bind(String(assetId), Number(versionNumber))
      .first();
    return row === null ? null : row;
  }

  async _findSubscriptionRow(assetId, userId) {
    const row = await this._db.prepare(
      `SELECT asset_id, subscriber_user_id, subscribed_at, last_seen_revision
       FROM asset_subscriptions WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(String(assetId), String(userId))
      .first();
    return row === null ? null : row;
  }

}

function normalizeVariantCreatePayload(payload, user) {
  const record = normalizeVariantRecord(payload.record, { expectedVariantId: '' });
  const timestamp = nowIso();
  return {
    assetId: record.variantId,
    assetType: 'variant',
    ownerUserId: String(user.userId),
    visibility: normalizeVisibility(payload.visibility),
    revision: 'r1',
    name: record.name,
    description: record.description,
    tags: record.tags,
    schemaVersion: null,
    variantKind: record.kind,
    baseNodeType: record.baseNodeType,
    serviceClass: record.serviceClass,
    operatorClass: record.operatorClass,
    createdAt: normalizeIsoString(record.createdAt, timestamp),
    updatedAt: timestamp,
    changeSummary: nullableString(payload.changeSummary),
    contentJson: stableJson({
      record: {
        ...record,
        createdAt: normalizeIsoString(record.createdAt, timestamp),
        updatedAt: timestamp,
      },
    }),
  };
}

function normalizeVariantUpdatePayload(payload, existing, user) {
  const record = normalizeVariantRecord(payload.record, { expectedVariantId: String(existing.asset_id) });
  const timestamp = nowIso();
  return {
    assetId: String(existing.asset_id),
    assetType: 'variant',
    ownerUserId: String(user.userId),
    visibility: normalizeVisibility(payload.visibility ?? existing.visibility),
    revision: String(payload.revision || payload.remoteRevision || ''),
    name: record.name,
    description: record.description,
    tags: record.tags,
    schemaVersion: null,
    variantKind: record.kind,
    baseNodeType: record.baseNodeType,
    serviceClass: record.serviceClass,
    operatorClass: record.operatorClass,
    createdAt: String(existing.created_at),
    updatedAt: timestamp,
    changeSummary: nullableString(payload.changeSummary),
    contentJson: stableJson({
      record: {
        ...record,
        createdAt: String(existing.created_at),
        updatedAt: timestamp,
      },
    }),
  };
}

function normalizeComponentCreatePayload(payload, user) {
  const record = normalizeComponentRecord(payload.record, { expectedComponentId: '' });
  const timestamp = nowIso();
  return {
    assetId: record.componentId,
    assetType: 'component',
    ownerUserId: String(user.userId),
    visibility: normalizeVisibility(payload.visibility),
    revision: 'r1',
    name: record.name,
    description: record.description,
    tags: record.tags,
    schemaVersion: record.schemaVersion,
    variantKind: null,
    baseNodeType: null,
    serviceClass: null,
    operatorClass: null,
    createdAt: normalizeIsoString(record.createdAt, timestamp),
    updatedAt: timestamp,
    changeSummary: nullableString(payload.changeSummary),
    contentJson: stableJson({
      record: {
        ...record,
        createdAt: normalizeIsoString(record.createdAt, timestamp),
        updatedAt: timestamp,
      },
    }),
  };
}

function normalizeComponentUpdatePayload(payload, existing, user) {
  const record = normalizeComponentRecord(payload.record, { expectedComponentId: String(existing.asset_id) });
  const timestamp = nowIso();
  return {
    assetId: String(existing.asset_id),
    assetType: 'component',
    ownerUserId: String(user.userId),
    visibility: normalizeVisibility(payload.visibility ?? existing.visibility),
    revision: String(payload.revision || ''),
    name: record.name,
    description: record.description,
    tags: record.tags,
    schemaVersion: record.schemaVersion,
    variantKind: null,
    baseNodeType: null,
    serviceClass: null,
    operatorClass: null,
    createdAt: String(existing.created_at),
    updatedAt: timestamp,
    changeSummary: nullableString(payload.changeSummary),
    contentJson: stableJson({
      record: {
        ...record,
        createdAt: String(existing.created_at),
        updatedAt: timestamp,
      },
    }),
  };
}

function normalizeVariantRecord(record, { expectedVariantId }) {
  if (!isPlainObject(record)) {
    throw new Error('record is required');
  }
  const variantId = requireNonEmptyString(record.variantId, 'record.variantId is required');
  if (expectedVariantId && variantId !== expectedVariantId) {
    throw new Error('record.variantId must match the request path');
  }
  const kind = requireNonEmptyString(record.kind, 'record.kind is required');
  const baseNodeType = requireNonEmptyString(record.baseNodeType, 'record.baseNodeType is required');
  const serviceClass = requireNonEmptyString(record.serviceClass, 'record.serviceClass is required');
  const name = requireNonEmptyString(record.name, 'record.name is required');
  const tags = normalizeTags(record.tags);
  if (!isPlainObject(record.spec)) {
    throw new Error('record.spec must be a JSON object');
  }
  return {
    variantId,
    kind,
    baseNodeType,
    serviceClass,
    operatorClass: nullableString(record.operatorClass),
    name,
    description: stringOrDefault(record.description, ''),
    tags,
    spec: deepCloneJson(record.spec),
    createdAt: normalizeIsoString(record.createdAt, ''),
    updatedAt: normalizeIsoString(record.updatedAt, ''),
  };
}

function normalizeComponentRecord(record, { expectedComponentId }) {
  if (!isPlainObject(record)) {
    throw new Error('record is required');
  }
  const componentId = requireNonEmptyString(record.componentId, 'record.componentId is required');
  if (expectedComponentId && componentId !== expectedComponentId) {
    throw new Error('record.componentId must match the request path');
  }
  const schemaVersion = requireNonEmptyString(record.schemaVersion, 'record.schemaVersion is required');
  if (schemaVersion !== COMPONENT_SCHEMA_VERSION) {
    throw new Error(`record.schemaVersion must be ${COMPONENT_SCHEMA_VERSION}`);
  }
  if (!isPlainObject(record.content)) {
    throw new Error('record.content must be a JSON object');
  }
  const contentSchemaVersion = requireNonEmptyString(record.content.schemaVersion, 'record.content.schemaVersion is required');
  if (contentSchemaVersion !== COMPONENT_SCHEMA_VERSION) {
    throw new Error(`record.content.schemaVersion must be ${COMPONENT_SCHEMA_VERSION}`);
  }
  const layout = record.content.layout;
  if (!isPlainObject(layout)) {
    throw new Error('record.content.layout must be a JSON object');
  }
  return {
    componentId,
    name: requireNonEmptyString(record.name, 'record.name is required'),
    description: stringOrDefault(record.description, ''),
    tags: normalizeTags(record.tags),
    schemaVersion,
    content: deepCloneJson(record.content),
    createdAt: normalizeIsoString(record.createdAt, ''),
    updatedAt: normalizeIsoString(record.updatedAt, ''),
  };
}

function assetPayloadFromRows({ head, version, subscription, viewerUserId }) {
  const content = parseJsonObject(version.content_json);
  const record = parseAssetRecord({ assetType: String(head.asset_type), content });
  const isOwner = String(head.owner_user_id) === String(viewerUserId || '');
  const isSubscribed = subscription !== null && subscription !== undefined && subscription.subscribed_at !== undefined && subscription.subscribed_at !== null;
  return {
    assetId: String(head.asset_id),
    assetType: String(head.asset_type),
    ownerUserId: String(head.owner_user_id),
    ownerDisplayName: nullableString(head.owner_display_name),
    visibility: String(head.visibility),
    revision: String(version.revision),
    latestRevision: String(head.latest_revision),
    versionNumber: Number(version.version_number),
    latestVersionNumber: Number(head.latest_version_number),
    changeSummary: nullableString(version.change_summary),
    createdAt: String(head.created_at),
    updatedAt: String(head.updated_at),
    isOwner,
    subscribed: isSubscribed,
    editable: isOwner,
    subscription: isSubscribed
      ? {
          subscribedAt: String(subscription.subscribed_at),
          lastSeenRevision: nullableString(subscription.last_seen_revision),
        }
      : null,
    record,
  };
}

function adminAssetPayloadFromRow(row) {
  const content = parseJsonObject(row.content_json);
  const record = parseAssetRecord({ assetType: String(row.asset_type), content });
  return {
    assetId: String(row.asset_id),
    assetType: String(row.asset_type),
    ownerUserId: String(row.owner_user_id),
    ownerDisplayName: nullableString(row.owner_display_name),
    visibility: String(row.visibility),
    revision: String(row.revision),
    latestRevision: String(row.latest_revision),
    versionNumber: Number(row.version_number),
    latestVersionNumber: Number(row.latest_version_number),
    changeSummary: nullableString(row.change_summary),
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
    deletedAt: nullableString(row.deleted_at),
    record,
  };
}

function parseAssetRecord({ assetType, content }) {
  const record = content.record;
  if (!isPlainObject(record)) {
    return {};
  }
  if (assetType === 'variant') {
    return {
      variantId: stringOrDefault(record.variantId, ''),
      kind: stringOrDefault(record.kind, ''),
      baseNodeType: stringOrDefault(record.baseNodeType, ''),
      serviceClass: stringOrDefault(record.serviceClass, ''),
      operatorClass: nullableString(record.operatorClass),
      name: stringOrDefault(record.name, ''),
      description: stringOrDefault(record.description, ''),
      tags: normalizeTags(record.tags),
      spec: isPlainObject(record.spec) ? deepCloneJson(record.spec) : {},
      createdAt: stringOrDefault(record.createdAt, ''),
      updatedAt: stringOrDefault(record.updatedAt, ''),
    };
  }
  return {
    componentId: stringOrDefault(record.componentId, ''),
    name: stringOrDefault(record.name, ''),
    description: stringOrDefault(record.description, ''),
    tags: normalizeTags(record.tags),
    schemaVersion: stringOrDefault(record.schemaVersion, COMPONENT_SCHEMA_VERSION),
    content: isPlainObject(record.content) ? deepCloneJson(record.content) : {},
    createdAt: stringOrDefault(record.createdAt, ''),
    updatedAt: stringOrDefault(record.updatedAt, ''),
  };
}

function applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner }) {
  const normalizedOwner = normalizeOwnerFilter(owner);
  const normalizedVisibility = normalizeVisibilityFilter(visibility);
  const viewerUserId = userId ? String(userId) : '';

  if (!viewerUserId) {
    filters.push(`h.visibility = 'public'`);
    if (normalizedOwner === 'me' || normalizedOwner === 'subscribed') {
      filters.push('1 = 0');
    }
    return;
  }

  if (normalizedOwner === 'me') {
    filters.push('h.owner_user_id = ?');
    bindings.push(viewerUserId);
  } else if (normalizedOwner === 'subscribed') {
    filters.push('EXISTS (SELECT 1 FROM asset_subscriptions s WHERE s.asset_id = h.asset_id AND s.subscriber_user_id = ?)');
    bindings.push(viewerUserId);
  } else if (normalizedOwner === 'public') {
    filters.push(`h.visibility = 'public'`);
  } else {
    filters.push('(h.visibility = \'public\' OR h.owner_user_id = ?)');
    bindings.push(viewerUserId);
  }

  if (normalizedVisibility === 'public') {
    filters.push(`h.visibility = 'public'`);
  } else if (normalizedVisibility === 'private') {
    filters.push('h.owner_user_id = ? AND h.visibility = ?');
    bindings.push(viewerUserId, 'private');
  }
}

function ensureCanView(head, userId) {
  const visibility = String(head.visibility);
  if (visibility === 'public') {
    return;
  }
  if (String(head.owner_user_id) !== String(userId || '')) {
    throw new AssetPermissionError('forbidden');
  }
}

function normalizeVisibility(value) {
  return String(value || 'private').trim() === 'public' ? 'public' : 'private';
}

function normalizeAssetType(value) {
  const text = String(value || '').trim();
  if (text !== 'variant' && text !== 'component') {
    throw new Error('assetType must be variant or component');
  }
  return text;
}

function normalizeOwnerFilter(value) {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  if (text === 'me' || text === 'subscribed' || text === 'public') {
    return text;
  }
  throw new Error('owner must be me, subscribed, or public');
}

function normalizeVisibilityFilter(value) {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  if (text === 'public' || text === 'private') {
    return text;
  }
  throw new Error('visibility must be public or private');
}

function normalizeVersionNumber(value) {
  const versionNumber = Number.parseInt(String(value), 10);
  if (!Number.isFinite(versionNumber) || versionNumber <= 0) {
    throw new Error('versionNumber must be a positive integer');
  }
  return versionNumber;
}

function nextRevisionForAsset(currentRevision) {
  const text = String(currentRevision || 'r0');
  if (!text.startsWith('r')) {
    return 'r1';
  }
  const value = Number.parseInt(text.slice(1), 10);
  if (!Number.isFinite(value) || value < 0) {
    return 'r1';
  }
  return `r${value + 1}`;
}

function normalizeTags(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item));
}

function parseCursor(value) {
  const cursor = Number.parseInt(String(value || '0'), 10);
  return Number.isFinite(cursor) && cursor >= 0 ? cursor : 0;
}

function normalizeIsoString(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function normalizeDbTimestamp(value) {
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return new Date(value).toISOString();
  }
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  const numeric = Number(text);
  if (Number.isFinite(numeric)) {
    return new Date(numeric).toISOString();
  }
  return text;
}

function stableJson(value) {
  return JSON.stringify(value);
}

function parseJsonObject(value) {
  try {
    const parsed = JSON.parse(String(value || '{}'));
    return isPlainObject(parsed) ? parsed : {};
  } catch (error) {
    return {};
  }
}

function deepCloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function requireNonEmptyString(value, message) {
  const text = String(value || '').trim();
  if (!text) {
    throw new Error(message);
  }
  return text;
}

function stringOrDefault(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function nullableString(value) {
  if (value === null || value === undefined) {
    return null;
  }
  const text = String(value).trim();
  return text ? text : null;
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function toBoolean(value) {
  if (typeof value === 'boolean') {
    return value;
  }
  const text = String(value || '').trim().toLowerCase();
  return text === '1' || text === 'true' || text === 'yes';
}
