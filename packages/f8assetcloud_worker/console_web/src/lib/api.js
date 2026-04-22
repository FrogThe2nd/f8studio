export class ApiError extends Error {
  constructor(status, message, payload = null) {
    super(message || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = Number(status || 0);
    this.payload = payload;
  }
}

async function parseJsonResponse(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }
  return JSON.parse(text);
}

export async function apiFetch(path, options = {}) {
  const { body, headers: providedHeaders, ...rest } = options;
  const headers = { ...(providedHeaders || {}) };
  const requestBody = body === undefined ? undefined : JSON.stringify(body);
  if (requestBody !== undefined && headers['Content-Type'] === undefined) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, {
    ...rest,
    headers,
    body: requestBody,
  });
  const payload = await parseJsonResponse(response);
  if (!response.ok) {
    throw new ApiError(response.status, String(payload?.message || `Request failed (${response.status})`), payload);
  }
  return payload;
}

function buildQuery(params) {
  const searchParams = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    const text = String(value ?? '').trim();
    if (text) {
      searchParams.set(key, text);
    }
  });
  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

export function getAuthProviders() {
  return apiFetch('/v1/auth/providers');
}

export function getSiteSettings() {
  return apiFetch('/v1/site-settings');
}

export function getCurrentUser() {
  return apiFetch('/v1/me');
}

export function updateCurrentUser(payload) {
  return apiFetch('/v1/me', {
    method: 'PUT',
    body: payload,
  });
}

export function requestPasswordReset(payload) {
  return apiFetch('/v1/auth/reset-password', {
    method: 'POST',
    body: payload,
  });
}

export function resolveAsset(assetId) {
  return apiFetch(`/v1/assets/${encodeURIComponent(assetId)}`);
}

export function getAssetDetail(assetType, assetId) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}`);
}

export function listAssetVersions(assetType, assetId, params = {}) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/versions${buildQuery(params)}`);
}

export function updateAssetVersionNote(assetType, assetId, versionNumber, payload) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionNumber)}`, {
    method: 'PATCH',
    body: payload,
  });
}

export function getAssetSubscribers(assetType, assetId, params = {}) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/subscribers${buildQuery(params)}`);
}

export function getAssetVersionContent(assetType, assetId, versionNumber) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionNumber)}/content`);
}

export function buildAssetDownloadPath(assetType, assetId, versionNumber = null) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  if (versionNumber === null || versionNumber === undefined || versionNumber === '') {
    return `${root}/${encodeURIComponent(assetId)}/download`;
  }
  return `${root}/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionNumber)}/download`;
}

export function listAssets(assetType, params = {}) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}${buildQuery(params)}`);
}

export function subscribeAsset(assetType, assetId) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/subscribe`, {
    method: 'POST',
  });
}

export function unsubscribeAsset(assetType, assetId) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/subscribe`, {
    method: 'DELETE',
  });
}

export function updateAssetMeta(assetType, assetId, payload) {
  const root = assetType === 'variant' ? '/v1/variants' : '/v1/components';
  return apiFetch(`${root}/${encodeURIComponent(assetId)}/meta`, {
    method: 'PATCH',
    body: payload,
  });
}

export function listManagedUsers(params = {}) {
  return apiFetch(`/v1/management/users${buildQuery(params)}`);
}

export function createManagedUser(payload) {
  return apiFetch('/v1/management/users', {
    method: 'POST',
    body: payload,
  });
}

export function updateManagedUser(userId, payload) {
  return apiFetch(`/v1/management/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    body: payload,
  });
}

export function deleteManagedUser(userId) {
  return apiFetch(`/v1/management/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
}

export function getManagedSiteSettings() {
  return apiFetch('/v1/management/site-settings');
}

export function updateManagedSiteSettings(payload) {
  return apiFetch('/v1/management/site-settings', {
    method: 'PUT',
    body: payload,
  });
}
