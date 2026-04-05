import { compressGzip, decompressGzip, escapeLikePattern, isPlainObject, nowIso, nullableString, stringOrDefault, toBoolean } from './utils.js';

const PAGE_SIZE = 100;
const MAX_CONTENT_BYTES = 10 * 1024 * 1024;
const COMPONENT_SCHEMA_VERSION = 'f8studio-session/1';

export class AssetConflictError extends Error {
  constructor({ assetId, assetType, revision }) {
    super(`Asset ${assetId} update conflict`);
    this.assetId = String(assetId);
    this.assetType = normalizeAssetType(assetType);
    this.revision = String(revision);
  }
}

export class AssetPermissionError extends Error {}
export class AssetNotFoundError extends Error {}
export class AssetValidationError extends Error {}

export class AssetRepository {
  constructor(db) {
    this._db = db;
  }

  async listUsers({ query, cursor }) {
    const filters = ['1 = 1'];
    const bindings = [];
    if (String(query || '').trim()) {
      const match = `%${escapeLikePattern(String(query).trim().toLowerCase())}%`;
      filters.push("(LOWER(COALESCE(u.username, '')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(u.displayUsername, u.name, '')) LIKE ? ESCAPE '\\' OR LOWER(u.email) LIKE ? ESCAPE '\\')");
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
        role: normalizeUserRole(row.role),
        isAdmin: normalizeUserRole(row.role) === 'admin',
        canUpload: normalizeUserRole(row.role) !== 'readonly',
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
      role: normalizeUserRole(row.role),
      isAdmin: normalizeUserRole(row.role) === 'admin',
      canUpload: normalizeUserRole(row.role) !== 'readonly',
      assetCount: Number(row.asset_count || 0),
      createdAt: normalizeDbTimestamp(row.createdAt),
      updatedAt: normalizeDbTimestamp(row.updatedAt),
    };
  }

  async getSiteSettings() {
    await this._ensureSiteSettingsRow();
    const row = await this._db.prepare(
      `SELECT allow_user_registration, updated_at, updated_by_user_id
       FROM site_settings
       WHERE id = 1`,
    ).first();
    return siteSettingsPayloadFromRow(row);
  }

  async updateSiteSettings({ allowUserRegistration, updatedByUserId }) {
    await this._ensureSiteSettingsRow();
    const current = await this.getSiteSettings();
    const nextAllowUserRegistration = (
      allowUserRegistration === undefined
        ? current.allowUserRegistration
        : toBoolean(allowUserRegistration)
    );
    await this._db.prepare(
      `UPDATE site_settings
       SET allow_user_registration = ?,
           updated_at = ?,
           updated_by_user_id = ?
       WHERE id = 1`,
    )
      .bind(
        nextAllowUserRegistration ? 1 : 0,
        nowIso(),
        nullableString(updatedByUserId),
      )
      .run();
    return this.getSiteSettings();
  }

  async listManagedAssets({ assetType, ownerUserId, query, includeDeleted, cursor }) {
    const filters = ['1 = 1'];
    const bindings = [];
    const normalizedAssetType = String(assetType || '').trim();
    if (normalizedAssetType) {
      filters.push('h.asset_type = ?');
      bindings.push(normalizeAssetType(normalizedAssetType));
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
      const match = `%${escapeLikePattern(String(query).trim().toLowerCase())}%`;
      filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(vd.base_node_type, '')) LIKE ? ESCAPE '\\')");
      bindings.push(match, match, match, match);
    }

    const start = parseCursor(cursor);
    const sql = `
      SELECT
        h.*,
        vd.variant_kind,
        vd.base_node_type,
        vd.service_class,
        vd.operator_class,
        COALESCE(u.displayUsername, u.name) AS owner_display_name,
        v.created_at AS version_created_at,
        v.created_by_user_id,
        v.change_summary,
        v.version_number,
        v.revision
      FROM asset_heads h
      LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
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
      entries: items.map((row) => adminAssetSummaryFromRow(row)),
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
    return adminAssetDetailFromRows({ head, version });
  }

  async adminDeleteAsset({ assetId }) {
    const existing = await this._findAssetHeadRow(assetId, { includeDeleted: true });
    if (existing === null) {
      return false;
    }
    const timestamp = nowIso();
    await this._db.prepare(
      `UPDATE asset_heads
       SET deleted_at = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(timestamp, timestamp, String(assetId))
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
    return this._getAssetDetailPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber: null });
  }

  async listVariants({ userId, kind, baseNodeType, query, visibility, owner, cursor }) {
    return this._listTypedAssetSummaries({
      assetType: 'variant',
      userId,
      query,
      cursor,
      visibility,
      owner,
      extraFilters: {
        variantKind: String(kind || '').trim(),
        baseNodeType: String(baseNodeType || '').trim(),
      },
    });
  }

  async listVariantVersions({ variantId, userId, cursor }) {
    return this._listAssetVersions({ assetId: variantId, assetType: 'variant', userId, cursor });
  }

  async getVariantVersion({ variantId, versionNumber, userId }) {
    return this._getAssetDetailPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber });
  }

  async getVariantContent({ variantId, userId }) {
    return this._getAssetContentPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber: null });
  }

  async getVariantVersionContent({ variantId, versionNumber, userId }) {
    return this._getAssetContentPayload({ assetId: variantId, assetType: 'variant', userId, versionNumber });
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

  async updateVariantVisibility({ variantId, visibility, revision, userId }) {
    return this._updateAssetVisibility({ assetId: variantId, assetType: 'variant', visibility, revision, userId });
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
    return this._getAssetDetailPayload({ assetId: componentId, assetType: 'component', userId, versionNumber: null });
  }

  async listComponents({ userId, query, visibility, owner, cursor }) {
    return this._listTypedAssetSummaries({
      assetType: 'component',
      userId,
      query,
      cursor,
      visibility,
      owner,
      extraFilters: {},
    });
  }

  async listComponentVersions({ componentId, userId, cursor }) {
    return this._listAssetVersions({ assetId: componentId, assetType: 'component', userId, cursor });
  }

  async getComponentVersion({ componentId, versionNumber, userId }) {
    return this._getAssetDetailPayload({ assetId: componentId, assetType: 'component', userId, versionNumber });
  }

  async getComponentContent({ componentId, userId }) {
    return this._getAssetContentPayload({ assetId: componentId, assetType: 'component', userId, versionNumber: null });
  }

  async getComponentVersionContent({ componentId, versionNumber, userId }) {
    return this._getAssetContentPayload({ assetId: componentId, assetType: 'component', userId, versionNumber });
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

  async updateComponentVisibility({ componentId, visibility, revision, userId }) {
    return this._updateAssetVisibility({ assetId: componentId, assetType: 'component', visibility, revision, userId });
  }

  async searchAssets({ assetType, userId, query, visibility, owner, cursor }) {
    return this._listAssetSummaries({
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
      throw new AssetValidationError('assetId already exists');
    }

    enforceContentSizeLimit(normalized.contentJson);
    await this._db.prepare(
      `INSERT INTO asset_heads (
         asset_id, asset_type, owner_user_id, visibility, latest_revision, latest_version_number,
         name, description, tags_json, schema_version, deleted_at, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)`,
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
        normalized.createdAt,
        normalized.updatedAt,
      )
      .run();

    if (normalized.assetType === 'variant') {
      await this._upsertVariantDetails(normalized.assetId, normalized.variantDetails);
    }

    await this._insertAssetVersion({
      assetId: normalized.assetId,
      versionNumber: 1,
      revision: normalized.revision,
      contentJson: normalized.contentJson,
      createdAt: normalized.updatedAt,
      createdByUserId: userId,
      changeSummary: normalized.changeSummary,
    });
    return this._getAssetDetailPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _updateAsset({ existing, normalized, userId }) {
    if (String(normalized.revision || '') !== String(existing.latest_revision || '')) {
      throw new AssetConflictError({
        assetId: existing.asset_id,
        assetType: existing.asset_type,
        revision: String(existing.latest_revision),
      });
    }

    enforceContentSizeLimit(normalized.contentJson);
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
        normalized.updatedAt,
        normalized.assetId,
      )
      .run();

    if (normalized.assetType === 'variant') {
      await this._upsertVariantDetails(normalized.assetId, normalized.variantDetails);
    } else {
      await this._deleteVariantDetails(normalized.assetId);
    }

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
      `UPDATE asset_subscriptions
       SET last_seen_revision = COALESCE(last_seen_revision, ?)
       WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(nextRevision, normalized.assetId, userId)
      .run();
    return this._getAssetDetailPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _deleteOwnedAsset({ assetId, assetType, userId }) {
    await this._requireOwnedAsset({ assetId, assetType, userId });
    await this._db.prepare(
      `UPDATE asset_heads SET deleted_at = ?, updated_at = ? WHERE asset_id = ?`,
    )
      .bind(nowIso(), nowIso(), String(assetId))
      .run();
  }

  async _getAssetContext({ assetId, assetType, userId, versionNumber }) {
    const head = await this.getAssetById(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);
    const targetVersionNumber = versionNumber === null ? Number(head.latest_version_number) : normalizeVersionNumber(versionNumber);
    if (targetVersionNumber !== Number(head.latest_version_number) && String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId || '')) {
      throw new AssetPermissionError('forbidden');
    }
    const version = await this.getAssetVersion(assetId, targetVersionNumber);
    if (version === null) {
      throw new AssetNotFoundError(`Asset version ${assetId}:${targetVersionNumber} not found`);
    }
    const subscription = userId ? await this._findSubscriptionRow(assetId, userId) : null;
    return { head, version, subscription };
  }

  async _getAssetDetailPayload({ assetId, assetType, userId, versionNumber }) {
    const { head, version, subscription } = await this._getAssetContext({ assetId, assetType, userId, versionNumber });
    return typedAssetDetailPayloadFromRows({ head, version, subscription, viewerUserId: userId });
  }

  async _getAssetContentPayload({ assetId, assetType, userId, versionNumber }) {
    const { head, version } = await this._getAssetContext({ assetId, assetType, userId, versionNumber });
    return typedAssetContentPayloadFromRows({ head, version });
  }

  async _listTypedAssetSummaries({ assetType, userId, query, cursor, visibility, owner, extraFilters }) {
    const filters = ['h.deleted_at IS NULL', 'h.asset_type = ?'];
    const bindings = [assetType];
    applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner });
    applyAssetQueryFilters({ filters, bindings, query, assetType, extraFilters });

    const start = parseCursor(cursor);
    const sql = `
      SELECT
        h.*,
        vd.variant_kind,
        vd.base_node_type,
        vd.service_class,
        vd.operator_class,
        COALESCE(u.displayUsername, u.name) AS owner_display_name,
        s.subscribed_at,
        s.last_seen_revision,
        v.created_by_user_id,
        v.change_summary,
        v.version_number,
        v.revision
      FROM asset_heads h
      LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
      JOIN asset_versions v
        ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
      LEFT JOIN user u ON u.id = h.owner_user_id
      LEFT JOIN asset_subscriptions s ON s.asset_id = h.asset_id AND s.subscriber_user_id = ?
      WHERE ${filters.join(' AND ')}
      ORDER BY LOWER(h.name), h.asset_id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(userId ? String(userId) : '', ...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => typedAssetSummaryPayloadFromRow(row, userId)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async _listAssetSummaries({ assetType, userId, query, cursor, visibility, owner, extraFilters }) {
    const filters = ['h.deleted_at IS NULL', 'h.asset_type = ?'];
    const bindings = [assetType];
    applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner });
    applyAssetQueryFilters({ filters, bindings, query, assetType, extraFilters });

    const start = parseCursor(cursor);
    const sql = `
      SELECT
        h.*,
        vd.variant_kind,
        vd.base_node_type,
        vd.service_class,
        vd.operator_class,
        COALESCE(u.displayUsername, u.name) AS owner_display_name,
        s.subscribed_at,
        s.last_seen_revision,
        v.created_at AS version_created_at,
        v.created_by_user_id,
        v.change_summary,
        v.version_number,
        v.revision
      FROM asset_heads h
      LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
      JOIN asset_versions v
        ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
      LEFT JOIN user u ON u.id = h.owner_user_id
      LEFT JOIN asset_subscriptions s ON s.asset_id = h.asset_id AND s.subscriber_user_id = ?
      WHERE ${filters.join(' AND ')}
      ORDER BY LOWER(h.name), h.asset_id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(userId ? String(userId) : '', ...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => searchAssetSummaryFromRow(row, userId)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async _listAssetVersions({ assetId, assetType, userId, cursor }) {
    const head = await this.getAssetById(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);

    const start = parseCursor(cursor);
    const result = await this._db.prepare(
      `SELECT asset_id, version_number, revision, content, created_at, created_by_user_id, change_summary
       FROM asset_versions
       WHERE asset_id = ?
       ORDER BY version_number DESC
       LIMIT ? OFFSET ?`,
    )
      .bind(String(assetId), PAGE_SIZE + 1, start)
      .all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      versions: items.map((row) => assetVersionSummaryFromRow(assetType, row)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async _subscribeAsset({ assetId, assetType, userId }) {
    const head = await this.getAssetById(assetId);
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
       DO UPDATE SET subscribed_at = excluded.subscribed_at, last_seen_revision = excluded.last_seen_revision`,
    )
      .bind(String(assetId), String(userId), now, String(head.latest_revision))
      .run();
    return this._getAssetDetailPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _unsubscribeAsset({ assetId, assetType, userId }) {
    const head = await this.getAssetById(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    await this._db.prepare(
      `DELETE FROM asset_subscriptions WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(String(assetId), String(userId))
      .run();
    return this._getAssetDetailPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _forkAsset({ assetId, assetType, payload, user }) {
    const source = await this._getAssetContentPayload({ assetId, assetType, userId: user.userId, versionNumber: null });
    const forkPayload = isPlainObject(payload) ? payload : {};
    if (assetType === 'variant') {
      const sourceRecord = source.record;
      return this.createVariant({
        payload: {
          record: {
            ...sourceRecord,
            variantId: stringOrDefault(forkPayload.variantId, crypto.randomUUID()),
            name: stringOrDefault(forkPayload.name, `${sourceRecord.name} Copy`),
          },
          visibility: stringOrDefault(forkPayload.visibility, 'private'),
          changeSummary: stringOrDefault(forkPayload.changeSummary, `Forked from ${assetId}`),
        },
        user,
      });
    }
    const sourceRecord = source.record;
    return this.createComponent({
      payload: {
        record: {
          ...sourceRecord,
          componentId: stringOrDefault(forkPayload.componentId, crypto.randomUUID()),
          name: stringOrDefault(forkPayload.name, `${sourceRecord.name} Copy`),
          content: deepCloneJson(sourceRecord.content),
        },
        visibility: stringOrDefault(forkPayload.visibility, 'private'),
        changeSummary: stringOrDefault(forkPayload.changeSummary, `Forked from ${assetId}`),
      },
      user,
    });
  }

  async _updateAssetVisibility({ assetId, assetType, visibility, revision, userId }) {
    const existing = await this._requireOwnedAsset({ assetId, assetType, userId });
    const expectedRevision = String(revision || '').trim();
    if (expectedRevision && expectedRevision !== String(existing.latest_revision || '')) {
      throw new AssetConflictError({
        assetId: String(existing.asset_id),
        assetType,
        revision: String(existing.latest_revision),
      });
    }
    await this._db.prepare(
      `UPDATE asset_heads
       SET visibility = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(normalizeVisibility(visibility), nowIso(), String(assetId))
      .run();
    return this._getAssetDetailPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _requireOwnedAsset({ assetId, assetType, userId }) {
    const existing = await this.getAssetById(assetId);
    if (existing === null || String(existing.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(existing.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    return existing;
  }

  async _insertAssetVersion({ assetId, versionNumber, revision, contentJson, createdAt, createdByUserId, changeSummary }) {
    const compressedContent = await compressGzip(contentJson);
    await this._db.prepare(
      `INSERT INTO asset_versions (
         asset_id, version_number, revision, content, created_at, created_by_user_id, change_summary
       ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        String(assetId),
        Number(versionNumber),
        String(revision),
        compressedContent,
        String(createdAt),
        String(createdByUserId),
        nullableString(changeSummary),
      )
      .run();
  }

  async _upsertVariantDetails(assetId, details) {
    await this._db.prepare(
      `INSERT INTO variant_details (
         asset_id, variant_kind, base_node_type, service_class, operator_class
       ) VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(asset_id)
       DO UPDATE SET
         variant_kind = excluded.variant_kind,
         base_node_type = excluded.base_node_type,
         service_class = excluded.service_class,
         operator_class = excluded.operator_class`,
    )
      .bind(
        String(assetId),
        String(details.variantKind),
        String(details.baseNodeType),
        String(details.serviceClass),
        nullableString(details.operatorClass),
      )
      .run();
  }

  async _deleteVariantDetails(assetId) {
    await this._db.prepare('DELETE FROM variant_details WHERE asset_id = ?')
      .bind(String(assetId))
      .run();
  }

  async _findAssetHeadRow(assetId, { includeDeleted = false } = {}) {
    const deletedFilter = includeDeleted ? '' : 'AND h.deleted_at IS NULL';
    const row = await this._db.prepare(
      `SELECT
         h.*,
         vd.variant_kind,
         vd.base_node_type,
         vd.service_class,
         vd.operator_class,
         COALESCE(u.displayUsername, u.name) AS owner_display_name
       FROM asset_heads h
       LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
       LEFT JOIN user u ON u.id = h.owner_user_id
       WHERE h.asset_id = ? ${deletedFilter}`,
    )
      .bind(String(assetId))
      .first();
    return row || null;
  }

  async _findAssetVersionRow(assetId, versionNumber) {
    const row = await this._db.prepare(
      `SELECT asset_id, version_number, revision, content, created_at, created_by_user_id, change_summary
       FROM asset_versions
       WHERE asset_id = ? AND version_number = ?`,
    )
      .bind(String(assetId), Number(versionNumber))
      .first();
    if (!row) {
      return null;
    }
    return this._inflateVersionRow(row);
  }

  async _inflateVersionRow(row) {
    return {
      ...row,
      content: await decodeVersionContent(row.content),
    };
  }

  async getAssetById(assetId) {
    return this._findAssetHeadRow(assetId);
  }

  async getAssetVersion(assetId, versionNumber) {
    return this._findAssetVersionRow(assetId, versionNumber);
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

  async hasAssets(userId) {
    const row = await this._db.prepare(
      'SELECT COUNT(*) AS count FROM asset_heads WHERE owner_user_id = ?',
    )
      .bind(String(userId))
      .first();
    return Number(row?.count || 0) > 0;
  }

  async _ensureSiteSettingsRow() {
    await this._db.prepare(
      `INSERT INTO site_settings (id, allow_user_registration, updated_at, updated_by_user_id)
       VALUES (1, 0, ?, NULL)
       ON CONFLICT(id) DO NOTHING`,
    )
      .bind(nowIso())
      .run();
  }
}

function enforceContentSizeLimit(contentJson) {
  const byteLength = new TextEncoder().encode(String(contentJson)).length;
  if (byteLength > MAX_CONTENT_BYTES) {
    throw new AssetValidationError(
      `content exceeds maximum allowed size (${Math.round(byteLength / 1024)}KB, limit ${Math.round(MAX_CONTENT_BYTES / 1024)}KB)`,
    );
  }
}

function normalizeVariantCreatePayload(payload, user) {
  const record = normalizeVariantRecord(payload.record, { expectedVariantId: '' });
  const timestamp = nowIso();
  const createdAt = normalizeIsoString(record.createdAt, timestamp);
  const updatedAt = timestamp;
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
    createdAt,
    updatedAt,
    changeSummary: nullableString(payload.changeSummary),
    variantDetails: {
      variantKind: record.kind,
      baseNodeType: record.baseNodeType,
      serviceClass: record.serviceClass,
      operatorClass: record.operatorClass,
    },
    contentJson: stableJson({
      record: {
        ...record,
        createdAt,
        updatedAt,
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
    createdAt: String(existing.created_at),
    updatedAt: timestamp,
    changeSummary: nullableString(payload.changeSummary),
    variantDetails: {
      variantKind: record.kind,
      baseNodeType: record.baseNodeType,
      serviceClass: record.serviceClass,
      operatorClass: record.operatorClass,
    },
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
  const createdAt = normalizeIsoString(record.createdAt, timestamp);
  const updatedAt = timestamp;
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
    createdAt,
    updatedAt,
    changeSummary: nullableString(payload.changeSummary),
    contentJson: stableJson({
      record: {
        ...record,
        createdAt,
        updatedAt,
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
    revision: String(payload.revision || payload.remoteRevision || ''),
    name: record.name,
    description: record.description,
    tags: record.tags,
    schemaVersion: record.schemaVersion,
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
    throw new AssetValidationError('record is required');
  }
  const variantId = requireNonEmptyString(record.variantId, 'record.variantId is required');
  if (expectedVariantId && variantId !== expectedVariantId) {
    throw new AssetValidationError('record.variantId must match the request path');
  }
  const kind = requireNonEmptyString(record.kind, 'record.kind is required');
  const baseNodeType = requireNonEmptyString(record.baseNodeType, 'record.baseNodeType is required');
  const serviceClass = requireNonEmptyString(record.serviceClass, 'record.serviceClass is required');
  const name = requireNonEmptyString(record.name, 'record.name is required');
  if (!isPlainObject(record.spec)) {
    throw new AssetValidationError('record.spec must be a JSON object');
  }
  return {
    variantId,
    kind,
    baseNodeType,
    serviceClass,
    operatorClass: nullableString(record.operatorClass),
    name,
    description: stringOrDefault(record.description, ''),
    tags: normalizeTags(record.tags),
    spec: deepCloneJson(record.spec),
    createdAt: normalizeIsoString(record.createdAt, ''),
    updatedAt: normalizeIsoString(record.updatedAt, ''),
  };
}

function normalizeComponentRecord(record, { expectedComponentId }) {
  if (!isPlainObject(record)) {
    throw new AssetValidationError('record is required');
  }
  const componentId = requireNonEmptyString(record.componentId, 'record.componentId is required');
  if (expectedComponentId && componentId !== expectedComponentId) {
    throw new AssetValidationError('record.componentId must match the request path');
  }
  const schemaVersion = requireNonEmptyString(record.schemaVersion, 'record.schemaVersion is required');
  if (schemaVersion !== COMPONENT_SCHEMA_VERSION) {
    throw new AssetValidationError(`record.schemaVersion must be ${COMPONENT_SCHEMA_VERSION}`);
  }
  if (!isPlainObject(record.content)) {
    throw new AssetValidationError('record.content must be a JSON object');
  }
  const contentSchemaVersion = requireNonEmptyString(record.content.schemaVersion, 'record.content.schemaVersion is required');
  if (contentSchemaVersion !== COMPONENT_SCHEMA_VERSION) {
    throw new AssetValidationError(`record.content.schemaVersion must be ${COMPONENT_SCHEMA_VERSION}`);
  }
  if (!isPlainObject(record.content.layout)) {
    throw new AssetValidationError('record.content.layout must be a JSON object');
  }
  return {
    componentId,
    name: requireNonEmptyString(record.name, 'record.name is required'),
    description: stringOrDefault(record.description, ''),
    usageNotes: stringOrDefault(record.usageNotes, ''),
    tags: normalizeTags(record.tags),
    schemaVersion,
    content: deepCloneJson(record.content),
    createdAt: normalizeIsoString(record.createdAt, ''),
    updatedAt: normalizeIsoString(record.updatedAt, ''),
  };
}

function typedAssetSummaryPayloadFromRow(row, viewerUserId) {
  if (String(row.asset_type) === 'variant') {
    return variantSummaryPayloadFromRow(row, viewerUserId);
  }
  return componentSummaryPayloadFromRow(row, viewerUserId);
}

function typedAssetDetailPayloadFromRows({ head, version, subscription, viewerUserId }) {
  if (String(head.asset_type) === 'variant') {
    return variantDetailPayloadFromRows({ head, version, subscription, viewerUserId });
  }
  return componentDetailPayloadFromRows({ head, version, subscription, viewerUserId });
}

function typedAssetContentPayloadFromRows({ head, version }) {
  if (String(head.asset_type) === 'variant') {
    return variantContentPayloadFromRows({ head, version });
  }
  return componentContentPayloadFromRows({ head, version });
}

function variantSummaryPayloadFromRow(row, viewerUserId) {
  return {
    ...genericTypedAssetPayload(row, viewerUserId),
    variantId: String(row.asset_id),
    variantKind: stringOrDefault(row.variant_kind, ''),
    baseNodeType: stringOrDefault(row.base_node_type, ''),
    serviceClass: stringOrDefault(row.service_class, ''),
    operatorClass: nullableString(row.operator_class),
    hasContent: true,
  };
}

function variantDetailPayloadFromRows({ head, version, subscription, viewerUserId }) {
  return {
    ...variantSummaryPayloadFromRow({ ...head, ...version, ...(subscription || {}) }, viewerUserId),
    versionCreatedAt: String(version.created_at),
    createdByUserId: String(version.created_by_user_id),
  };
}

function variantContentPayloadFromRows({ head, version }) {
  return {
    variantId: String(head.asset_id),
    assetType: 'variant',
    versionNumber: Number(version.version_number),
    revision: String(version.revision),
    record: parseVariantRecord(version.content),
  };
}

function componentSummaryPayloadFromRow(row, viewerUserId) {
  return {
    ...genericTypedAssetPayload(row, viewerUserId),
    componentId: String(row.asset_id),
    schemaVersion: stringOrDefault(row.schema_version, COMPONENT_SCHEMA_VERSION),
    hasContent: true,
  };
}

function componentDetailPayloadFromRows({ head, version, subscription, viewerUserId }) {
  return {
    ...componentSummaryPayloadFromRow({ ...head, ...version, ...(subscription || {}) }, viewerUserId),
    versionCreatedAt: String(version.created_at),
    createdByUserId: String(version.created_by_user_id),
  };
}

function componentContentPayloadFromRows({ head, version }) {
  return {
    componentId: String(head.asset_id),
    assetType: 'component',
    versionNumber: Number(version.version_number),
    revision: String(version.revision),
    record: parseComponentRecord(version.content),
  };
}

function genericTypedAssetPayload(row, viewerUserId) {
  const isOwner = String(row.owner_user_id) === String(viewerUserId || '');
  const isSubscribed = hasSubscription(row);
  return {
    assetType: String(row.asset_type),
    ownerUserId: String(row.owner_user_id),
    ownerDisplayName: nullableString(row.owner_display_name),
    visibility: String(row.visibility),
    revision: String(row.revision),
    latestRevision: String(row.latest_revision),
    versionNumber: Number(row.version_number),
    latestVersionNumber: Number(row.latest_version_number),
    changeSummary: nullableString(row.change_summary),
    name: String(row.name),
    description: String(row.description),
    tags: normalizeTags(parseJsonArray(row.tags_json)),
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
    isOwner,
    subscribed: isSubscribed,
    editable: isOwner,
    subscription: isSubscribed
      ? {
          subscribedAt: String(row.subscribed_at),
          lastSeenRevision: nullableString(row.last_seen_revision),
        }
      : null,
  };
}

function adminAssetDetailFromRows({ head, version }) {
  const summary = adminAssetSummaryFromRow({ ...head, ...version });
  if (String(head.asset_type) === 'variant') {
    return {
      ...summary,
      record: parseVariantRecord(version.content),
    };
  }
  return {
    ...summary,
    record: parseComponentRecord(version.content),
  };
}

function adminAssetSummaryFromRow(row) {
  const base = genericAssetSummary(row);
  if (String(row.asset_type) === 'variant') {
    return {
      ...base,
      variantKind: stringOrDefault(row.variant_kind, ''),
      baseNodeType: stringOrDefault(row.base_node_type, ''),
      serviceClass: stringOrDefault(row.service_class, ''),
      operatorClass: nullableString(row.operator_class),
    };
  }
  return {
    ...base,
    schemaVersion: nullableString(row.schema_version),
  };
}

function searchAssetSummaryFromRow(row, viewerUserId) {
  const base = genericAssetSummary(row);
  const isOwner = String(row.owner_user_id) === String(viewerUserId || '');
  const isSubscribed = hasSubscription(row);
  if (String(row.asset_type) === 'variant') {
    return {
      ...base,
      subscribed: isSubscribed,
      editable: isOwner,
      variantKind: stringOrDefault(row.variant_kind, ''),
      baseNodeType: stringOrDefault(row.base_node_type, ''),
    };
  }
  return {
    ...base,
    subscribed: isSubscribed,
    editable: isOwner,
    schemaVersion: nullableString(row.schema_version),
  };
}

function siteSettingsPayloadFromRow(row) {
  return {
    allowUserRegistration: Number(row?.allow_user_registration ?? 0) !== 0,
    updatedAt: normalizeDbTimestamp(row?.updated_at),
    updatedByUserId: nullableString(row?.updated_by_user_id),
  };
}

function normalizeUserRole(value) {
  const role = String(value || '').trim().toLowerCase();
  if (role === 'admin' || role === 'readonly') {
    return role;
  }
  return 'user';
}

function genericAssetSummary(row) {
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
    name: String(row.name),
    description: String(row.description),
    tags: normalizeTags(parseJsonArray(row.tags_json)),
    createdAt: String(row.created_at),
    updatedAt: String(row.updated_at),
    deletedAt: nullableString(row.deleted_at),
  };
}

function assetVersionSummaryFromRow(assetType, row) {
  const summary = {
    assetType,
    versionNumber: Number(row.version_number),
    revision: String(row.revision),
    createdAt: String(row.created_at),
    createdByUserId: String(row.created_by_user_id),
    changeSummary: nullableString(row.change_summary),
  };
  if (assetType === 'variant') {
    return { variantId: String(row.asset_id), ...summary };
  }
  return { componentId: String(row.asset_id), ...summary };
}

function applyAssetQueryFilters({ filters, bindings, query, assetType, extraFilters }) {
  if (query) {
    const match = `%${escapeLikePattern(String(query).trim().toLowerCase())}%`;
    filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(vd.base_node_type, '')) LIKE ? ESCAPE '\\')");
    bindings.push(match, match, match, match);
  }
  if (assetType === 'variant') {
    if (extraFilters.variantKind) {
      filters.push('vd.variant_kind = ?');
      bindings.push(String(extraFilters.variantKind));
    }
    if (extraFilters.baseNodeType) {
      filters.push('vd.base_node_type = ?');
      bindings.push(String(extraFilters.baseNodeType));
    }
  }
}

function parseVariantRecord(content) {
  const record = extractRecordEnvelope(parseJsonObject(content));
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

function parseComponentRecord(content) {
  const record = extractRecordEnvelope(parseJsonObject(content));
  return {
    componentId: stringOrDefault(record.componentId, ''),
    name: stringOrDefault(record.name, ''),
    description: stringOrDefault(record.description, ''),
    usageNotes: stringOrDefault(record.usageNotes, ''),
    tags: normalizeTags(record.tags),
    schemaVersion: stringOrDefault(record.schemaVersion, COMPONENT_SCHEMA_VERSION),
    content: isPlainObject(record.content) ? deepCloneJson(record.content) : {},
    createdAt: stringOrDefault(record.createdAt, ''),
    updatedAt: stringOrDefault(record.updatedAt, ''),
  };
}

function extractRecordEnvelope(payload) {
  if (!isPlainObject(payload.record)) {
    return {};
  }
  return payload.record;
}

function parseJsonArray(value) {
  if (typeof value !== 'string') {
    return [];
  }
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner }) {
  const normalizedOwner = normalizeOwnerFilter(owner);
  const normalizedVisibility = normalizeVisibilityFilter(visibility);
  const viewerUserId = userId ? String(userId) : '';

  if (!viewerUserId) {
    filters.push("h.visibility = 'public'");
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
    filters.push("h.visibility = 'public'");
  } else {
    filters.push("(h.visibility = 'public' OR h.owner_user_id = ?)");
    bindings.push(viewerUserId);
  }

  if (normalizedVisibility === 'public') {
    filters.push("h.visibility = 'public'");
  } else if (normalizedVisibility === 'private') {
    filters.push('h.owner_user_id = ? AND h.visibility = ?');
    bindings.push(viewerUserId, 'private');
  }
}

function ensureCanView(head, userId) {
  if (String(head.visibility) === 'public') {
    return;
  }
  if (String(head.owner_user_id) !== String(userId || '')) {
    throw new AssetPermissionError('forbidden');
  }
}

function hasSubscription(subscription) {
  return subscription !== null
    && subscription !== undefined
    && subscription.subscribed_at !== undefined
    && subscription.subscribed_at !== null;
}

function normalizeVisibility(value) {
  return String(value || 'private').trim() === 'public' ? 'public' : 'private';
}

function normalizeAssetType(value) {
  const text = String(value || '').trim();
  if (text !== 'variant' && text !== 'component') {
    throw new AssetValidationError('assetType must be variant or component');
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
  throw new AssetValidationError('owner must be me, subscribed, or public');
}

function normalizeVisibilityFilter(value) {
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  if (text === 'public' || text === 'private') {
    return text;
  }
  throw new AssetValidationError('visibility must be public or private');
}

function normalizeVersionNumber(value) {
  const versionNumber = Number.parseInt(String(value), 10);
  if (!Number.isFinite(versionNumber) || versionNumber <= 0) {
    throw new AssetValidationError('versionNumber must be a positive integer');
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
  return value.map((item) => String(item)).filter((item) => item.trim().length > 0);
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

async function decodeVersionContent(value) {
  if (value === null || value === undefined) {
    return {};
  }
  if (typeof value === 'string') {
    return parseJsonObject(value);
  }
  try {
    const text = await decompressGzip(value);
    return parseJsonObject(text);
  } catch (error) {
    try {
      return parseJsonObject(new TextDecoder().decode(value));
    } catch (decodeError) {
      console.error('decodeVersionContent: failed to decode version content', decodeError);
      return {};
    }
  }
}

function parseJsonObject(value) {
  if (isPlainObject(value)) {
    return deepCloneJson(value);
  }
  try {
    const parsed = JSON.parse(String(value || '{}'));
    if (!isPlainObject(parsed)) {
      return {};
    }
    return parsed;
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
    throw new AssetValidationError(message);
  }
  return text;
}
