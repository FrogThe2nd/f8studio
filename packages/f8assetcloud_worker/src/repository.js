import { canonicalUtcTimestamp, compressGzip, decompressGzip, escapeLikePattern, isPlainObject, nowIso, nullableString, stringOrDefault, toBoolean } from './utils.js';

const PAGE_SIZE = 100;
const MAX_CONTENT_BYTES = 10 * 1024 * 1024;
const MAX_ASSET_ID_LENGTH = 128;
const MAX_ASSET_NAME_LENGTH = 120;
const MAX_DESCRIPTION_BYTES = 16 * 1024;
const MAX_CHANGE_SUMMARY_LENGTH = 280;
const MAX_TAG_COUNT = 20;
const MAX_TAG_LENGTH = 48;
const MAX_ASSET_FIELD_LENGTH = 128;
const SAFE_ASSET_ID_PATTERN = /^[A-Za-z0-9._~-]+$/;
const COMPONENT_SCHEMA_VERSION = 'f8studio-session/1';
const textEncoder = new TextEncoder();

export class AssetConflictError extends Error {
  constructor({ assetId, assetType, versionNumber }) {
    super(`Asset ${assetId} update conflict`);
    this.assetId = String(assetId);
    this.assetType = normalizeAssetType(assetType);
    this.versionNumber = normalizeVersionNumber(versionNumber);
  }
}

export class AssetPermissionError extends Error { }
export class AssetNotFoundError extends Error { }
export class AssetValidationError extends Error { }

export class AssetRepository {
  constructor(db) {
    this._db = db;
    this._hasVariantDetailsTableCached = null;
  }

  async listUsers({ query, cursor }) {
    const filters = ['1 = 1'];
    const bindings = [];
    if (String(query || '').trim()) {
      const match = `%${escapeLikePattern(String(query).trim().toLowerCase())}%`;
      filters.push("(LOWER(u.name) LIKE ? ESCAPE '\\' OR LOWER(u.email) LIKE ? ESCAPE '\\')");
      bindings.push(match, match);
    }
    const start = parseCursor(cursor);
    const sql = `
      SELECT
        u.id,
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
        GROUP BY owner_user_id
      ) assets ON assets.owner_user_id = u.id
      WHERE ${filters.join(' AND ')}
      ORDER BY LOWER(u.name), u.id
      LIMIT ? OFFSET ?
    `;
    const result = await this._db.prepare(sql).bind(...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => ({
        userId: String(row.id),
        name: stringOrDefault(row.name, String(row.email || '')),
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
      name: stringOrDefault(row.name, String(row.email || '')),
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

  async isUserNameTaken({ name, excludeUserId = '' }) {
    const normalizedName = String(name || '').trim();
    if (!normalizedName) {
      return false;
    }
    const row = await this._db.prepare(
      `SELECT 1
       FROM user
       WHERE name = ?
         AND (? = '' OR id != ?)
       LIMIT 1`,
    )
      .bind(normalizedName, String(excludeUserId || ''), String(excludeUserId || ''))
      .first();
    return row !== null;
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

  async adminPurgeAllAssets() {
    const deletedSubscriptionsResult = await this._db.prepare(
      'DELETE FROM asset_subscriptions',
    ).run();
    const deletedVersionsResult = await this._db.prepare(
      'DELETE FROM asset_versions',
    ).run();
    let deletedVariantDetails = 0;
    if (await this._hasVariantDetailsTable()) {
      const deletedVariantDetailsResult = await this._db.prepare(
        'DELETE FROM variant_details',
      ).run();
      deletedVariantDetails = Number(deletedVariantDetailsResult.meta?.changes || 0);
    }
    const deletedHeadsResult = await this._db.prepare(
      'DELETE FROM asset_heads',
    ).run();
    return {
      deletedAssetSubscriptions: Number(deletedSubscriptionsResult.meta?.changes || 0),
      deletedAssetVersions: Number(deletedVersionsResult.meta?.changes || 0),
      deletedVariantDetails,
      deletedAssets: Number(deletedHeadsResult.meta?.changes || 0),
    };
  }

  async listManagedAssets({ assetType, ownerUserId, query, cursor, kind = '', baseNodeType = '' }) {
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
    if (String(query || '').trim()) {
      const match = `%${escapeLikePattern(String(query).trim().toLowerCase())}%`;
      if (normalizedAssetType === 'variant') {
        filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(vd.base_node_type, '')) LIKE ? ESCAPE '\\')");
        bindings.push(match, match, match, match);
      } else {
        filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\')");
        bindings.push(match, match, match);
      }
    }
    if (normalizedAssetType === 'variant') {
      const normalizedKind = String(kind || '').trim();
      if (normalizedKind) {
        filters.push('vd.variant_kind = ?');
        bindings.push(normalizedKind);
      }
      const normalizedBaseNodeType = String(baseNodeType || '').trim();
      if (normalizedBaseNodeType) {
        filters.push('vd.base_node_type = ?');
        bindings.push(normalizedBaseNodeType);
      }
    }

    const start = parseCursor(cursor);
    const sql = normalizedAssetType === 'variant'
      ? `
        SELECT
          h.*,
          vd.variant_kind,
          vd.base_node_type,
          vd.service_class,
          vd.operator_class,
          u.name AS owner_display_name,
          v.created_at AS version_created_at,
          v.created_by_user_id,
          v.change_summary,
          v.version_number
        FROM asset_heads h
        LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
        JOIN asset_versions v
          ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
        LEFT JOIN user u ON u.id = h.owner_user_id
        WHERE ${filters.join(' AND ')}
        ORDER BY h.updated_at DESC, h.asset_id
        LIMIT ? OFFSET ?
      `
      : `
        SELECT
          h.*,
          u.name AS owner_display_name,
          v.created_at AS version_created_at,
          v.created_by_user_id,
          v.change_summary,
          v.version_number
        FROM asset_heads h
        JOIN asset_versions v
          ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
        LEFT JOIN user u ON u.id = h.owner_user_id
        WHERE ${filters.join(' AND ')}
        ORDER BY h.updated_at DESC, h.asset_id
        LIMIT ? OFFSET ?
      `;
    const result = await this._db.prepare(sql).bind(...bindings, PAGE_SIZE + 1, start).all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    const resolvedItems = normalizedAssetType === 'variant'
      ? items
      : await this._mergeVariantDetailsIntoRows(items);
    return {
      entries: resolvedItems.map((row) => adminAssetSummaryFromRow(row)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async getManagedAsset({ assetId, assetTypeHint = '' }) {
    const head = await this._findAssetHeadRow(assetId, { assetTypeHint });
    if (head === null) {
      return null;
    }
    if (assetTypeHint && String(head.asset_type) !== String(assetTypeHint)) {
      return null;
    }
    const version = await this._findAssetVersionRow(assetId, Number(head.current_version_number));
    if (version === null) {
      return null;
    }
    return adminAssetDetailFromRows({ head, version });
  }

  async adminDeleteAsset({ assetId, assetTypeHint = '' }) {
    const existing = await this._findAssetHeadRow(assetId, { assetTypeHint });
    if (existing === null) {
      return false;
    }
    if (assetTypeHint && String(existing.asset_type) !== String(assetTypeHint)) {
      return false;
    }
    const assetIdStr = String(assetId);

    await this._db.prepare('DELETE FROM asset_subscriptions WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();

    await this._db.prepare('DELETE FROM asset_versions WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();

    if (await this._hasVariantDetailsTable()) {
      await this._db.prepare('DELETE FROM variant_details WHERE asset_id = ?')
        .bind(assetIdStr)
        .run();
    }

    await this._db.prepare('DELETE FROM asset_heads WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();

    return true;
  }

  async adminUpdateAssetVisibility({ assetId, visibility, assetTypeHint = '' }) {
    const existing = await this._findAssetHeadRow(assetId, { assetTypeHint });
    if (existing === null) {
      return null;
    }
    if (assetTypeHint && String(existing.asset_type) !== String(assetTypeHint)) {
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
    return this.getManagedAsset({ assetId, assetTypeHint });
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

  async listVariantSubscribers({ variantId, userId, cursor }) {
    return this._listAssetSubscribers({ assetId: variantId, assetType: 'variant', userId, cursor });
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

  async updateVariantVisibility({ variantId, visibility, versionNumber, userId }) {
    return this._updateAssetVisibility({ assetId: variantId, assetType: 'variant', visibility, versionNumber, userId });
  }

  async updateVariantVersionNote({ variantId, versionNumber, changeSummary, userId }) {
    return this._updateAssetVersionNote({
      assetId: variantId,
      assetType: 'variant',
      versionNumber,
      changeSummary,
      userId,
    });
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

  async listComponentSubscribers({ componentId, userId, cursor }) {
    return this._listAssetSubscribers({ assetId: componentId, assetType: 'component', userId, cursor });
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

  async updateComponentVisibility({ componentId, visibility, versionNumber, userId }) {
    return this._updateAssetVisibility({ assetId: componentId, assetType: 'component', visibility, versionNumber, userId });
  }

  async updateComponentVersionNote({ componentId, versionNumber, changeSummary, userId }) {
    return this._updateAssetVersionNote({
      assetId: componentId,
      assetType: 'component',
      versionNumber,
      changeSummary,
      userId,
    });
  }

  async _createAsset({ normalized, userId }) {
    const existing = await this._findAssetHeadRow(normalized.assetId);
    if (existing !== null) {
      throw new AssetValidationError('assetId already exists');
    }

    enforceContentSizeLimit(normalized.contentJson);
    await this._db.prepare(
      `INSERT INTO asset_heads (
         asset_id, asset_type, owner_user_id, visibility, current_version_number,
         name, description, tags_json, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        normalized.assetId,
        normalized.assetType,
        normalized.ownerUserId,
        normalized.visibility,
        1,
        normalized.name,
        normalized.description,
        JSON.stringify(normalized.tags),
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
      contentJson: normalized.contentJson,
      createdAt: normalized.updatedAt,
      createdByUserId: userId,
      changeSummary: normalized.changeSummary,
    });
    return this._getAssetDetailPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _updateAsset({ existing, normalized, userId }) {
    if (normalized.versionNumber !== null && Number(normalized.versionNumber) !== Number(existing.current_version_number)) {
      throw new AssetConflictError({
        assetId: existing.asset_id,
        assetType: existing.asset_type,
        versionNumber: Number(existing.current_version_number),
      });
    }

    enforceContentSizeLimit(normalized.contentJson);
    const nextVersionNumber = Number(existing.current_version_number) + 1;
    await this._db.prepare(
      `UPDATE asset_heads
       SET visibility = ?,
           current_version_number = ?,
           name = ?,
           description = ?,
           tags_json = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(
        normalized.visibility,
        nextVersionNumber,
        normalized.name,
        normalized.description,
        JSON.stringify(normalized.tags),
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
      contentJson: normalized.contentJson,
      createdAt: normalized.updatedAt,
      createdByUserId: userId,
      changeSummary: normalized.changeSummary,
    });
    await this._db.prepare(
      `UPDATE asset_subscriptions
       SET last_seen_version_number = COALESCE(last_seen_version_number, ?)
       WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(nextVersionNumber, normalized.assetId, userId)
      .run();
    return this._getAssetDetailPayload({ assetId: normalized.assetId, assetType: normalized.assetType, userId, versionNumber: null });
  }

  async _deleteOwnedAsset({ assetId, assetType, userId }) {
    await this._requireOwnedAsset({ assetId, assetType, userId });
    const assetIdStr = String(assetId);

    await this._db.prepare('DELETE FROM asset_subscriptions WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();

    await this._db.prepare('DELETE FROM asset_versions WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();

    if (await this._hasVariantDetailsTable()) {
      await this._db.prepare('DELETE FROM variant_details WHERE asset_id = ?')
        .bind(assetIdStr)
        .run();
    }

    await this._db.prepare('DELETE FROM asset_heads WHERE asset_id = ?')
      .bind(assetIdStr)
      .run();
  }

  async _getAssetContext({ assetId, assetType, userId, versionNumber }) {
    const head = await this.getAssetById(assetId, assetType);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);
    const targetVersionNumber = versionNumber === null ? Number(head.current_version_number) : normalizeVersionNumber(versionNumber);
    if (targetVersionNumber !== Number(head.current_version_number) && String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId || '')) {
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
    return typedAssetDetailPayloadFromRows({
      head,
      version,
      subscription,
      viewerUserId: userId,
      includeVersionNumber: versionNumber !== null,
    });
  }

  async _getAssetContentPayload({ assetId, assetType, userId, versionNumber }) {
    const { head, version } = await this._getAssetContext({ assetId, assetType, userId, versionNumber });
    return typedAssetContentPayloadFromRows({ head, version });
  }

  async _listTypedAssetSummaries({ assetType, userId, query, cursor, visibility, owner, extraFilters }) {
    const filters = ['h.asset_type = ?'];
    const bindings = [assetType];
    applyVisibilityOwnerFilters({ filters, bindings, userId, visibility, owner });
    applyAssetQueryFilters({ filters, bindings, query, assetType, extraFilters });

    const start = parseCursor(cursor);
    const sql = assetType === 'variant'
      ? `
        SELECT
          h.*,
          vd.variant_kind,
          vd.base_node_type,
          vd.service_class,
          vd.operator_class,
          u.name AS owner_display_name,
          s.subscribed_at,
          s.last_seen_version_number,
          v.created_by_user_id,
          v.change_summary,
          v.version_number
        FROM asset_heads h
        LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
        JOIN asset_versions v
          ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
        LEFT JOIN user u ON u.id = h.owner_user_id
        LEFT JOIN asset_subscriptions s ON s.asset_id = h.asset_id AND s.subscriber_user_id = ?
        WHERE ${filters.join(' AND ')}
        ORDER BY LOWER(h.name), h.asset_id
        LIMIT ? OFFSET ?
      `
      : `
        SELECT
          h.*,
          u.name AS owner_display_name,
          s.subscribed_at,
          s.last_seen_version_number,
          v.created_by_user_id,
          v.change_summary,
          v.version_number
        FROM asset_heads h
        JOIN asset_versions v
          ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
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

  async _listAssetVersions({ assetId, assetType, userId, cursor }) {
    const head = await this.getAssetById(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    ensureCanView(head, userId);

    const start = parseCursor(cursor);
    const result = await this._db.prepare(
      `SELECT asset_id, version_number, content, created_at, created_by_user_id, change_summary
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

  async _listAssetSubscribers({ assetId, assetType, userId, cursor }) {
    const head = await this.getAssetById(assetId);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(head.owner_user_id) !== String(userId || '')) {
      throw new AssetPermissionError('forbidden');
    }

    const start = parseCursor(cursor);
    const result = await this._db.prepare(
      `SELECT
         s.subscriber_user_id,
         s.subscribed_at,
         s.last_seen_version_number,
         u.name AS subscriber_name
       FROM asset_subscriptions s
       JOIN user u ON u.id = s.subscriber_user_id
       WHERE s.asset_id = ?
       ORDER BY LOWER(COALESCE(u.name, s.subscriber_user_id)), s.subscriber_user_id
       LIMIT ? OFFSET ?`,
    )
      .bind(String(assetId), PAGE_SIZE + 1, start)
      .all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const hasMore = rows.length > PAGE_SIZE;
    const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
    return {
      entries: items.map((row) => assetSubscriberFromRow(row)),
      nextCursor: hasMore ? String(start + PAGE_SIZE) : null,
    };
  }

  async _subscribeAsset({ assetId, assetType, userId }) {
    const head = await this.getAssetById(assetId, assetType);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(head.visibility) !== 'public' && String(head.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    const now = nowIso();
    await this._db.prepare(
      `INSERT INTO asset_subscriptions (asset_id, subscriber_user_id, subscribed_at, last_seen_version_number)
       VALUES (?, ?, ?, ?)
       ON CONFLICT(asset_id, subscriber_user_id)
       DO UPDATE SET subscribed_at = excluded.subscribed_at, last_seen_version_number = excluded.last_seen_version_number`,
    )
      .bind(String(assetId), String(userId), now, Number(head.current_version_number))
      .run();
    return this._getAssetDetailPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _unsubscribeAsset({ assetId, assetType, userId }) {
    const head = await this.getAssetById(assetId, assetType);
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

  async _updateAssetVersionNote({ assetId, assetType, versionNumber, changeSummary, userId }) {
    const head = await this.getAssetById(assetId, assetType);
    if (head === null || String(head.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(head.owner_user_id) !== String(userId || '')) {
      throw new AssetPermissionError('forbidden');
    }
    const normalizedVersionNumber = normalizeVersionNumber(versionNumber);
    const existingVersion = await this.getAssetVersion(assetId, normalizedVersionNumber);
    if (existingVersion === null) {
      throw new AssetNotFoundError(`Asset version ${assetId}:${normalizedVersionNumber} not found`);
    }
    await this._db.prepare(
      `UPDATE asset_versions
       SET change_summary = ?
       WHERE asset_id = ? AND version_number = ?`,
    )
      .bind(normalizeChangeSummary(changeSummary), String(assetId), normalizedVersionNumber)
      .run();
    return this._getAssetDetailPayload({
      assetId,
      assetType,
      userId,
      versionNumber: normalizedVersionNumber,
    });
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

  async _updateAssetVisibility({ assetId, assetType, visibility, versionNumber, userId }) {
    const existing = await this._requireOwnedAsset({ assetId, assetType, userId });
    const expectedVersionNumber = normalizeOptionalVersionNumber(versionNumber);
    if (expectedVersionNumber !== null && expectedVersionNumber !== Number(existing.current_version_number)) {
      throw new AssetConflictError({
        assetId: String(existing.asset_id),
        assetType,
        versionNumber: Number(existing.current_version_number),
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

  async updateVariantMeta({ variantId, payload, user }) {
    return this._updateAssetMeta({ assetId: variantId, assetType: 'variant', payload, userId: user.userId });
  }

  async updateComponentMeta({ componentId, payload, user }) {
    return this._updateAssetMeta({ assetId: componentId, assetType: 'component', payload, userId: user.userId });
  }

  async _updateAssetMeta({ assetId, assetType, payload, userId }) {
    // Metadata-only update: updates name/description/tags without creating a new version row.
    await this._requireOwnedAsset({ assetId, assetType, userId });
    const name = requireAssetName(payload.name, 'name is required');
    const description = optionalDescription(payload.description);
    const tags = normalizeTags(payload.tags);
    await this._db.prepare(
      `UPDATE asset_heads
       SET name = ?,
           description = ?,
           tags_json = ?,
           updated_at = ?
       WHERE asset_id = ?`,
    )
      .bind(name, description, JSON.stringify(tags), nowIso(), String(assetId))
      .run();
    return this._getAssetDetailPayload({ assetId, assetType, userId, versionNumber: null });
  }

  async _requireOwnedAsset({ assetId, assetType, userId }) {
    const existing = await this.getAssetById(assetId, assetType);
    if (existing === null || String(existing.asset_type) !== assetType) {
      throw new AssetNotFoundError(`Asset ${assetId} not found`);
    }
    if (String(existing.owner_user_id) !== String(userId)) {
      throw new AssetPermissionError('forbidden');
    }
    return existing;
  }

  async _insertAssetVersion({ assetId, versionNumber, contentJson, createdAt, createdByUserId, changeSummary }) {
    const compressedContent = await compressGzip(contentJson);
    await this._db.prepare(
      `INSERT INTO asset_versions (
         asset_id, version_number, content, created_at, created_by_user_id, change_summary
       ) VALUES (?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        String(assetId),
        Number(versionNumber),
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

  async _findAssetHeadRow(assetId, { assetTypeHint = '' } = {}) {
    const row = assetTypeHint === 'variant'
      ? await this._db.prepare(
        `SELECT
           h.*,
           vd.variant_kind,
           vd.base_node_type,
           vd.service_class,
           vd.operator_class,
           u.name AS owner_display_name
         FROM asset_heads h
         JOIN asset_versions v
           ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
         LEFT JOIN variant_details vd ON vd.asset_id = h.asset_id
         LEFT JOIN user u ON u.id = h.owner_user_id
         WHERE h.asset_id = ?`,
      )
        .bind(String(assetId))
        .first()
      : await this._db.prepare(
        `SELECT
           h.*,
           u.name AS owner_display_name
        FROM asset_heads h
         JOIN asset_versions v
           ON v.asset_id = h.asset_id AND v.version_number = h.current_version_number
         LEFT JOIN user u ON u.id = h.owner_user_id
         WHERE h.asset_id = ?`,
      )
        .bind(String(assetId))
        .first();
    if (row === null) {
      return null;
    }
    if (assetTypeHint === 'variant') {
      return row;
    }
    return this._mergeVariantDetailsIntoRow(row);
  }

  async _findAssetVersionRow(assetId, versionNumber) {
    const row = await this._db.prepare(
      `SELECT asset_id, version_number, content, created_at, created_by_user_id, change_summary
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

  async getAssetById(assetId, assetTypeHint = '') {
    return this._findAssetHeadRow(assetId, { assetTypeHint });
  }

  async getAssetVersion(assetId, versionNumber) {
    return this._findAssetVersionRow(assetId, versionNumber);
  }

  async _findSubscriptionRow(assetId, userId) {
    const row = await this._db.prepare(
      `SELECT asset_id, subscriber_user_id, subscribed_at, last_seen_version_number
       FROM asset_subscriptions WHERE asset_id = ? AND subscriber_user_id = ?`,
    )
      .bind(String(assetId), String(userId))
      .first();
    return row === null ? null : row;
  }

  async _hasVariantDetailsTable() {
    if (this._hasVariantDetailsTableCached !== null) {
      return this._hasVariantDetailsTableCached;
    }
    const row = await this._db.prepare(
      `SELECT name
       FROM sqlite_master
       WHERE type = 'table' AND name = 'variant_details'
       LIMIT 1`,
    ).first();
    const hasTable = row !== null;
    this._hasVariantDetailsTableCached = hasTable;
    return hasTable;
  }

  async _findVariantDetailsRow(assetId) {
    if (!await this._hasVariantDetailsTable()) {
      return null;
    }
    const row = await this._db.prepare(
      `SELECT asset_id, variant_kind, base_node_type, service_class, operator_class
       FROM variant_details
       WHERE asset_id = ?`,
    )
      .bind(String(assetId))
      .first();
    return row === null ? null : row;
  }

  async _findVariantDetailsRows(assetIds) {
    if (assetIds.length === 0 || !await this._hasVariantDetailsTable()) {
      return new Map();
    }
    const placeholders = assetIds.map(() => '?').join(', ');
    const result = await this._db.prepare(
      `SELECT asset_id, variant_kind, base_node_type, service_class, operator_class
       FROM variant_details
       WHERE asset_id IN (${placeholders})`,
    )
      .bind(...assetIds)
      .all();
    const rows = Array.isArray(result.results) ? result.results : [];
    const byAssetId = new Map();
    for (const row of rows) {
      byAssetId.set(String(row.asset_id), row);
    }
    return byAssetId;
  }

  async _mergeVariantDetailsIntoRow(row) {
    if (String(row.asset_type) !== 'variant') {
      return row;
    }
    const details = await this._findVariantDetailsRow(String(row.asset_id));
    if (details === null) {
      return row;
    }
    return {
      ...row,
      variant_kind: details.variant_kind,
      base_node_type: details.base_node_type,
      service_class: details.service_class,
      operator_class: details.operator_class,
    };
  }

  async _mergeVariantDetailsIntoRows(rows) {
    const variantAssetIds = [];
    for (const row of rows) {
      if (String(row.asset_type) === 'variant') {
        variantAssetIds.push(String(row.asset_id));
      }
    }
    const detailsByAssetId = await this._findVariantDetailsRows(variantAssetIds);
    return rows.map((row) => {
      if (String(row.asset_type) !== 'variant') {
        return row;
      }
      const details = detailsByAssetId.get(String(row.asset_id));
      if (details === undefined) {
        return row;
      }
      return {
        ...row,
        variant_kind: details.variant_kind,
        base_node_type: details.base_node_type,
        service_class: details.service_class,
        operator_class: details.operator_class,
      };
    });
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
    versionNumber: null,
    name: record.name,
    description: record.description,
    tags: record.tags,
    createdAt,
    updatedAt,
    changeSummary: normalizeChangeSummary(payload.changeSummary),
    variantDetails: {
      variantKind: record.kind,
      baseNodeType: record.baseNodeType,
      serviceClass: record.serviceClass,
      operatorClass: record.operatorClass,
    },
    contentJson: stableJson(record.spec),
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
    versionNumber: normalizeOptionalVersionNumber(payload.versionNumber),
    name: record.name,
    description: record.description,
    tags: record.tags,
    createdAt: String(existing.created_at),
    updatedAt: timestamp,
    changeSummary: normalizeChangeSummary(payload.changeSummary),
    variantDetails: {
      variantKind: record.kind,
      baseNodeType: record.baseNodeType,
      serviceClass: record.serviceClass,
      operatorClass: record.operatorClass,
    },
    contentJson: stableJson(record.spec),
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
    versionNumber: null,
    name: record.name,
    description: record.description,
    tags: record.tags,
    createdAt,
    updatedAt,
    changeSummary: normalizeChangeSummary(payload.changeSummary),
    contentJson: stableJson(record.content),
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
    versionNumber: normalizeOptionalVersionNumber(payload.versionNumber),
    name: record.name,
    description: record.description,
    tags: record.tags,
    createdAt: String(existing.created_at),
    updatedAt: timestamp,
    changeSummary: normalizeChangeSummary(payload.changeSummary),
    contentJson: stableJson(record.content),
  };
}

function normalizeVariantRecord(record, { expectedVariantId }) {
  if (!isPlainObject(record)) {
    throw new AssetValidationError('record is required');
  }
  const variantId = requireAssetIdentifier(record.variantId, 'record.variantId is required');
  if (expectedVariantId && variantId !== expectedVariantId) {
    throw new AssetValidationError('record.variantId must match the request path');
  }
  const kind = requireAssetField(record.kind, 'record.kind is required');
  const baseNodeType = requireAssetField(record.baseNodeType, 'record.baseNodeType is required');
  const serviceClass = requireAssetField(record.serviceClass, 'record.serviceClass is required');
  const name = requireAssetName(record.name, 'record.name is required');
  if (!isPlainObject(record.spec)) {
    throw new AssetValidationError('record.spec must be a JSON object');
  }
  return {
    variantId,
    kind,
    baseNodeType,
    serviceClass,
    operatorClass: optionalAssetField(record.operatorClass),
    name,
    description: optionalDescription(record.description),
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
  if (Object.hasOwn(record, 'schemaVersion')) {
    throw new AssetValidationError('record.schemaVersion is not allowed; use record.content.schemaVersion');
  }
  const componentId = requireAssetIdentifier(record.componentId, 'record.componentId is required');
  if (expectedComponentId && componentId !== expectedComponentId) {
    throw new AssetValidationError('record.componentId must match the request path');
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
    name: requireAssetName(record.name, 'record.name is required'),
    description: optionalDescription(record.description),
    tags: normalizeTags(record.tags),
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

function typedAssetDetailPayloadFromRows({ head, version, subscription, viewerUserId, includeVersionNumber }) {
  if (String(head.asset_type) === 'variant') {
    return variantDetailPayloadFromRows({ head, version, subscription, viewerUserId, includeVersionNumber });
  }
  return componentDetailPayloadFromRows({ head, version, subscription, viewerUserId, includeVersionNumber });
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

function variantDetailPayloadFromRows({ head, version, subscription, viewerUserId, includeVersionNumber }) {
  const payload = {
    ...variantSummaryPayloadFromRow(
      {
        ...head,
        ...(subscription || {}),
        created_by_user_id: version.created_by_user_id,
        change_summary: version.change_summary,
        version_number: version.version_number,
      },
      viewerUserId,
    ),
    versionCreatedAt: normalizeDbTimestamp(version.created_at),
    createdByUserId: String(version.created_by_user_id),
  };
  if (includeVersionNumber) {
    payload.versionNumber = Number(version.version_number);
  }
  return payload;
}

function variantContentPayloadFromRows({ head, version }) {
  return {
    variantId: String(head.asset_id),
    assetType: 'variant',
    versionNumber: Number(version.version_number),
    record: parseVariantRecord(version.content, { head, version }),
  };
}

function componentSummaryPayloadFromRow(row, viewerUserId) {
  return {
    ...genericTypedAssetPayload(row, viewerUserId),
    componentId: String(row.asset_id),
    hasContent: true,
  };
}

function componentDetailPayloadFromRows({ head, version, subscription, viewerUserId, includeVersionNumber }) {
  const payload = {
    ...componentSummaryPayloadFromRow(
      {
        ...head,
        ...(subscription || {}),
        created_by_user_id: version.created_by_user_id,
        change_summary: version.change_summary,
        version_number: version.version_number,
      },
      viewerUserId,
    ),
    versionCreatedAt: normalizeDbTimestamp(version.created_at),
    createdByUserId: String(version.created_by_user_id),
  };
  if (includeVersionNumber) {
    payload.versionNumber = Number(version.version_number);
  }
  return payload;
}

function componentContentPayloadFromRows({ head, version }) {
  return {
    componentId: String(head.asset_id),
    assetType: 'component',
    versionNumber: Number(version.version_number),
    record: parseComponentRecord(version.content, { head, version }),
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
    versionNumber: Number(row.version_number ?? row.current_version_number),
    changeSummary: nullableString(row.change_summary),
    name: String(row.name),
    description: String(row.description),
    tags: normalizeTags(parseJsonArray(row.tags_json)),
    createdAt: normalizeDbTimestamp(row.created_at),
    updatedAt: normalizeDbTimestamp(row.updated_at),
    isOwner,
    subscribed: isSubscribed,
    editable: isOwner,
    subscription: isSubscribed
      ? {
        subscribedAt: normalizeDbTimestamp(row.subscribed_at),
        lastSeenVersionNumber: nullableNumber(row.last_seen_version_number),
      }
      : null,
  };
}

function assetSubscriberFromRow(row) {
  return {
    userId: String(row.subscriber_user_id),
    name: stringOrDefault(row.subscriber_name, String(row.subscriber_user_id || '')),
    subscribedAt: normalizeDbTimestamp(row.subscribed_at),
    lastSeenVersionNumber: nullableNumber(row.last_seen_version_number),
  };
}

function adminAssetDetailFromRows({ head, version }) {
  const summary = adminAssetSummaryFromRow({
    ...head,
    created_by_user_id: version.created_by_user_id,
    change_summary: version.change_summary,
    version_number: version.version_number,
  });
  if (String(head.asset_type) === 'variant') {
    return {
      ...summary,
      record: parseVariantRecord(version.content, { head, version }),
    };
  }
  return {
    ...summary,
    record: parseComponentRecord(version.content, { head, version }),
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
  return base;
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
    versionNumber: Number(row.version_number ?? row.current_version_number),
    changeSummary: nullableString(row.change_summary),
    name: String(row.name),
    description: String(row.description),
    tags: normalizeTags(parseJsonArray(row.tags_json)),
    createdAt: normalizeDbTimestamp(row.created_at),
    updatedAt: normalizeDbTimestamp(row.updated_at),
  };
}

function assetVersionSummaryFromRow(assetType, row) {
  const summary = {
    assetType,
    versionNumber: Number(row.version_number),
    createdAt: normalizeDbTimestamp(row.created_at),
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
    if (assetType === 'variant') {
      filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(vd.base_node_type, '')) LIKE ? ESCAPE '\\')");
      bindings.push(match, match, match, match);
    } else {
      filters.push("(LOWER(h.name) LIKE ? ESCAPE '\\' OR LOWER(h.description) LIKE ? ESCAPE '\\' OR LOWER(h.tags_json) LIKE ? ESCAPE '\\')");
      bindings.push(match, match, match);
    }
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

function parseVariantRecord(content, { head = null, version = null } = {}) {
  const specPayload = normalizeVariantContentPayload(parseJsonObject(content));
  const variantId = stringOrDefault(head?.asset_id, '');
  const createdAt = normalizeDbTimestamp(head?.created_at) || normalizeDbTimestamp(version?.created_at);
  const updatedAt = normalizeDbTimestamp(version?.created_at) || normalizeDbTimestamp(head?.updated_at) || createdAt;
  return {
    variantId,
    kind: stringOrDefault(head?.variant_kind, ''),
    baseNodeType: stringOrDefault(head?.base_node_type, ''),
    serviceClass: stringOrDefault(head?.service_class, ''),
    operatorClass: nullableString(head?.operator_class),
    name: stringOrDefault(head?.name, variantId),
    description: stringOrDefault(head?.description, ''),
    tags: normalizeTags(parseJsonArray(head?.tags_json)),
    spec: specPayload,
    createdAt,
    updatedAt,
  };
}

function parseComponentRecord(content, { head = null, version = null } = {}) {
  const contentPayload = normalizeComponentContentPayload(parseJsonObject(content));
  const componentId = stringOrDefault(head?.asset_id, '');
  const createdAt = normalizeDbTimestamp(head?.created_at) || normalizeDbTimestamp(version?.created_at);
  const updatedAt = normalizeDbTimestamp(version?.created_at) || normalizeDbTimestamp(head?.updated_at) || createdAt;
  return {
    componentId,
    name: stringOrDefault(head?.name, componentId),
    description: stringOrDefault(head?.description, ''),
    tags: normalizeTags(parseJsonArray(head?.tags_json)),
    content: contentPayload,
    createdAt,
    updatedAt,
  };
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

function normalizeOptionalVersionNumber(value) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return null;
  }
  return normalizeVersionNumber(value);
}

function nullableNumber(value) {
  if (value === undefined || value === null || String(value).trim() === '') {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeTags(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  if (value.length > MAX_TAG_COUNT) {
    throw new AssetValidationError(`tags must contain at most ${MAX_TAG_COUNT} items`);
  }
  const tags = value
    .map((item) => requireConstrainedString(item, 'tag must be a non-empty string', {
      maxLength: MAX_TAG_LENGTH,
      fieldLabel: 'tag',
    }))
    .filter((item) => item.length > 0);
  if (tags.length > MAX_TAG_COUNT) {
    throw new AssetValidationError(`tags must contain at most ${MAX_TAG_COUNT} items`);
  }
  return tags;
}

function parseCursor(value) {
  const cursor = Number.parseInt(String(value || '0'), 10);
  return Number.isFinite(cursor) && cursor >= 0 ? cursor : 0;
}

function normalizeIsoString(value, fallback) {
  const text = String(value || '').trim();
  if (text) {
    return normalizeDbTimestamp(text);
  }
  return normalizeDbTimestamp(fallback);
}

function normalizeDbTimestamp(value) {
  if (value instanceof Date) {
    return canonicalUtcTimestamp(value);
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return canonicalUtcTimestamp(value);
  }
  const text = String(value || '').trim();
  if (!text) {
    return '';
  }
  const numeric = Number(text);
  if (Number.isFinite(numeric)) {
    return canonicalUtcTimestamp(numeric);
  }
  const normalized = canonicalUtcTimestamp(text);
  return normalized || text;
}

function stableJson(value) {
  return JSON.stringify(value);
}

function normalizeVariantSpecPayload(spec) {
  if (!isPlainObject(spec)) {
    throw new AssetValidationError('variant version payload must be a JSON object');
  }
  return deepCloneJson(spec);
}

function normalizeVariantContentPayload(content) {
  if (!isPlainObject(content)) {
    throw new AssetValidationError('stored variant content must be the raw spec JSON object');
  }
  if (looksLikeStoredRecordEnvelope(content) || looksLikeStoredVariantRecord(content)) {
    throw new AssetValidationError('stored variant content must be the raw spec JSON object without record or envelope metadata');
  }
  return normalizeVariantSpecPayload(content);
}

function normalizeComponentContentPayload(content) {
  if (!isPlainObject(content)) {
    throw new AssetValidationError(`stored component content must be the canonical session payload { schemaVersion, layout }`);
  }
  if (looksLikeStoredRecordEnvelope(content) || looksLikeStoredComponentRecord(content)) {
    throw new AssetValidationError(`stored component content must be the canonical session payload { schemaVersion, layout }`);
  }
  const schemaVersion = requireNonEmptyString(content.schemaVersion, 'component version payload schemaVersion is required');
  if (schemaVersion !== COMPONENT_SCHEMA_VERSION) {
    throw new AssetValidationError(`component version payload schemaVersion must be ${COMPONENT_SCHEMA_VERSION}`);
  }
  if (!isPlainObject(content.layout)) {
    throw new AssetValidationError('component version payload layout must be a JSON object');
  }
  return deepCloneJson(content);
}

async function decodeVersionContent(value) {
  if (value === null || value === undefined) {
    throw new AssetValidationError('stored version content is missing');
  }
  if (typeof value === 'string') {
    return parseJsonObject(value, 'stored version content must be a JSON object');
  }
  try {
    const binaryValue = toBinaryContent(value);
    if (binaryValue !== null) {
      try {
        return parseJsonObject(await decompressGzip(binaryValue), 'stored version content must be a JSON object');
      } catch (gzipError) {
        throw new AssetValidationError('stored version content must be valid gzip-compressed JSON');
      }
    }
    if (value instanceof Uint8Array) {
      return parseJsonObject(new TextDecoder().decode(value), 'stored version content must be a JSON object');
    }
    if (ArrayBuffer.isView(value)) {
      return parseJsonObject(new TextDecoder().decode(value), 'stored version content must be a JSON object');
    }
    if (value instanceof ArrayBuffer) {
      return parseJsonObject(new TextDecoder().decode(new Uint8Array(value)), 'stored version content must be a JSON object');
    }
    return parseJsonObject(String(value), 'stored version content must be a JSON object');
  } catch (decodeError) {
    if (decodeError instanceof AssetValidationError) {
      throw decodeError;
    }
    console.error('decodeVersionContent: failed to decode version content', decodeError);
    throw new AssetValidationError('stored version content must be a JSON object');
  }
}

function looksLikeStoredRecordEnvelope(payload) {
  return isPlainObject(payload) && isPlainObject(payload.record);
}

function looksLikeStoredVariantRecord(payload) {
  return isPlainObject(payload)
    && Object.hasOwn(payload, 'variantId')
    && isPlainObject(payload.spec);
}

function looksLikeStoredComponentRecord(payload) {
  return isPlainObject(payload)
    && Object.hasOwn(payload, 'componentId')
    && isPlainObject(payload.content);
}

function toBinaryContent(value) {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  if (Array.isArray(value)) {
    return numericArrayToUint8Array(value);
  }
  if (!isPlainObject(value)) {
    return null;
  }
  if (Array.isArray(value.data)) {
    return numericArrayToUint8Array(value.data);
  }
  if (Array.isArray(value.bytes)) {
    return numericArrayToUint8Array(value.bytes);
  }
  if (typeof value.base64 === 'string' && value.base64) {
    return base64ToUint8Array(value.base64);
  }
  return null;
}

function numericArrayToUint8Array(values) {
  const bytes = [];
  for (const value of values) {
    const numberValue = Number(value);
    if (!Number.isInteger(numberValue) || numberValue < 0 || numberValue > 255) {
      return null;
    }
    bytes.push(numberValue);
  }
  return new Uint8Array(bytes);
}

function base64ToUint8Array(value) {
  const decoded = atob(String(value));
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

function parseJsonObject(value, errorMessage = 'stored version content must be a JSON object') {
  if (isPlainObject(value)) {
    return deepCloneJson(value);
  }
  try {
    const parsed = JSON.parse(String(value || '{}'));
    if (!isPlainObject(parsed)) {
      throw new AssetValidationError(errorMessage);
    }
    return parsed;
  } catch (error) {
    if (error instanceof AssetValidationError) {
      throw error;
    }
    throw new AssetValidationError(errorMessage);
  }
}

function deepCloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function requireAssetIdentifier(value, message) {
  const text = requireConstrainedString(value, message, {
    maxLength: MAX_ASSET_ID_LENGTH,
    fieldLabel: 'asset id',
  });
  if (!SAFE_ASSET_ID_PATTERN.test(text)) {
    throw new AssetValidationError('asset id must use URL-safe characters only');
  }
  return text;
}

function requireAssetName(value, message) {
  return requireConstrainedString(value, message, {
    maxLength: MAX_ASSET_NAME_LENGTH,
    fieldLabel: 'name',
  });
}

function requireAssetField(value, message) {
  return requireConstrainedString(value, message, {
    maxLength: MAX_ASSET_FIELD_LENGTH,
    fieldLabel: 'field',
  });
}

function optionalAssetField(value) {
  const text = nullableString(value);
  if (text === null) {
    return null;
  }
  return requireConstrainedString(text, 'field is too long', {
    maxLength: MAX_ASSET_FIELD_LENGTH,
    fieldLabel: 'field',
  });
}

function optionalDescription(value) {
  const text = String(value || '');
  ensureMaxByteLength(text, MAX_DESCRIPTION_BYTES, 'description');
  return text;
}

function normalizeChangeSummary(value) {
  const text = nullableString(value);
  if (text === null) {
    return null;
  }
  if (text.length > MAX_CHANGE_SUMMARY_LENGTH) {
    throw new AssetValidationError(`changeSummary must be at most ${MAX_CHANGE_SUMMARY_LENGTH} characters`);
  }
  return text;
}

function requireConstrainedString(value, message, { maxLength, fieldLabel }) {
  const text = String(value || '').trim();
  if (!text) {
    throw new AssetValidationError(message);
  }
  if (text.length > Number(maxLength)) {
    throw new AssetValidationError(`${fieldLabel} must be at most ${maxLength} characters`);
  }
  return text;
}

function ensureMaxByteLength(value, maxBytes, fieldLabel) {
  const byteLength = textEncoder.encode(String(value || '')).length;
  if (byteLength > Number(maxBytes)) {
    throw new AssetValidationError(`${fieldLabel} must be at most ${Math.round(Number(maxBytes) / 1024)} KiB`);
  }
}

function requireNonEmptyString(value, message) {
  const text = String(value || '').trim();
  if (!text) {
    throw new AssetValidationError(message);
  }
  return text;
}
