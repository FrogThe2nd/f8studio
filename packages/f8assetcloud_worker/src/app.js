import { hashPassword, issueTokenPair, verifyJwt, verifyPassword } from './auth.js';
import { AssetConflictError, AssetNotFoundError, AssetPermissionError, AssetRepository } from './repository.js';

export function createApp() {
  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      if (request.method === 'OPTIONS') {
        return jsonResponse(204, {});
      }
      try {
        validateEnv(env);
        const repo = new AssetRepository(env.DB);
        await ensureBootstrapUser(env, repo);

        if (!url.pathname.startsWith('/v1/')) {
          if (request.method === 'GET' || request.method === 'HEAD') {
            return await serveFrontend(request, env, url);
          }
          return jsonResponse(404, { message: 'not found' });
        }

        if (request.method === 'POST' && url.pathname === '/v1/auth/register') {
          const payload = await readJsonBody(request);
          const result = await register({ env, repo, payload });
          return jsonResponse(200, result);
        }
        if (request.method === 'POST' && url.pathname === '/v1/auth/login') {
          const payload = await readJsonBody(request);
          const result = await login({ env, repo, username: payload.username, password: payload.password });
          return jsonResponse(200, result);
        }
        if (request.method === 'POST' && url.pathname === '/v1/auth/refresh') {
          const payload = await readJsonBody(request);
          const result = await refreshAuth({ env, repo, refreshToken: payload.refreshToken });
          return jsonResponse(200, result);
        }
        if (request.method === 'POST' && url.pathname === '/v1/auth/logout') {
          const authUser = await requireAuthenticatedUser({ request, env, repo });
          await repo.revokeRefreshTokensForUser(authUser.userId);
          return jsonResponse(200, {});
        }
        if (url.pathname.startsWith('/v1/admin/')) {
          const adminUser = await requireAdminUser({ request, env, repo });
          return await routeAdminRequest({ request, url, repo, adminUser });
        }
        if (request.method === 'GET' && url.pathname === '/v1/me') {
          const user = await requireAuthenticatedUser({ request, env, repo });
          return jsonResponse(200, {
            userId: user.userId,
            username: user.username,
            displayName: user.displayName,
            isAdmin: user.isAdmin,
          });
        }
        if (request.method === 'POST' && url.pathname === '/v1/me/password') {
          const user = await requireAuthenticatedUser({ request, env, repo });
          const payload = await readJsonBody(request);
          await changePassword({ repo, user, payload });
          return jsonResponse(200, {});
        }
        if (request.method === 'GET' && url.pathname === '/v1/search') {
          const viewer = await optionalAuthenticatedUser({ request, env, repo });
          const result = await repo.searchAssets({
            assetType: url.searchParams.get('assetType') || '',
            userId: viewer === null ? null : viewer.userId,
            query: url.searchParams.get('q') || '',
            visibility: url.searchParams.get('visibility') || '',
            owner: url.searchParams.get('owner') || '',
            cursor: url.searchParams.get('cursor') || '',
          });
          return jsonResponse(200, result);
        }
        if (request.method === 'GET' && url.pathname === '/v1/variants') {
          const viewer = await optionalAuthenticatedUser({ request, env, repo });
          const result = await repo.listVariants({
            userId: viewer === null ? null : viewer.userId,
            kind: url.searchParams.get('kind') || '',
            baseNodeType: url.searchParams.get('baseNodeType') || '',
            query: url.searchParams.get('q') || '',
            visibility: url.searchParams.get('visibility') || '',
            owner: url.searchParams.get('owner') || '',
            cursor: url.searchParams.get('cursor') || '',
          });
          return jsonResponse(200, result);
        }
        if (request.method === 'POST' && url.pathname === '/v1/variants') {
          const user = await requireAuthenticatedUser({ request, env, repo });
          const payload = await readJsonBody(request);
          return jsonResponse(200, await repo.createVariant({ payload, user }));
        }
        if (request.method === 'GET' && url.pathname === '/v1/components') {
          const viewer = await optionalAuthenticatedUser({ request, env, repo });
          const result = await repo.listComponents({
            userId: viewer === null ? null : viewer.userId,
            query: url.searchParams.get('q') || '',
            visibility: url.searchParams.get('visibility') || '',
            owner: url.searchParams.get('owner') || '',
            cursor: url.searchParams.get('cursor') || '',
          });
          return jsonResponse(200, result);
        }
        if (request.method === 'POST' && url.pathname === '/v1/components') {
          const user = await requireAuthenticatedUser({ request, env, repo });
          const payload = await readJsonBody(request);
          return jsonResponse(200, await repo.createComponent({ payload, user }));
        }
        if (url.pathname.startsWith('/v1/variants/')) {
          return await routeAssetRequest({ request, env, repo, url, assetType: 'variant' });
        }
        if (url.pathname.startsWith('/v1/components/')) {
          return await routeAssetRequest({ request, env, repo, url, assetType: 'component' });
        }
        return jsonResponse(404, { message: 'not found' });
      } catch (error) {
        return handleError(error);
      }
    },
  };
}

async function routeAssetRequest({ request, env, repo, url, assetType }) {
  const prefix = assetType === 'variant' ? '/v1/variants/' : '/v1/components/';
  const tail = decodeURIComponent(url.pathname.slice(prefix.length));
  const parts = tail.split('/').filter((part) => part.length > 0);
  const assetId = parts[0] || '';
  if (!assetId) {
    return jsonResponse(404, { message: 'not found' });
  }

  if (parts.length === 1) {
    if (request.method === 'GET') {
      const viewer = await optionalAuthenticatedUser({ request, env, repo });
      const result = assetType === 'variant'
        ? await repo.getVariant({ variantId: assetId, userId: viewer === null ? null : viewer.userId })
        : await repo.getComponent({ componentId: assetId, userId: viewer === null ? null : viewer.userId });
      return jsonResponse(200, result);
    }
    if (request.method === 'PUT') {
      const user = await requireAuthenticatedUser({ request, env, repo });
      const payload = await readJsonBody(request);
      const result = assetType === 'variant'
        ? await repo.updateVariant({ variantId: assetId, payload, user })
        : await repo.updateComponent({ componentId: assetId, payload, user });
      return jsonResponse(200, result);
    }
    if (request.method === 'DELETE') {
      const user = await requireAuthenticatedUser({ request, env, repo });
      if (assetType === 'variant') {
        await repo.deleteVariant({ variantId: assetId, userId: user.userId });
      } else {
        await repo.deleteComponent({ componentId: assetId, userId: user.userId });
      }
      return jsonResponse(200, {});
    }
  }

  if (parts.length === 2 && parts[1] === 'versions' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ request, env, repo });
    const result = assetType === 'variant'
      ? await repo.listVariantVersions({ variantId: assetId, userId: viewer === null ? null : viewer.userId })
      : await repo.listComponentVersions({ componentId: assetId, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 3 && parts[1] === 'versions' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ request, env, repo });
    const versionNumber = parts[2];
    const result = assetType === 'variant'
      ? await repo.getVariantVersion({ variantId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId })
      : await repo.getComponentVersion({ componentId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 2 && parts[1] === 'subscribe') {
    const user = await requireAuthenticatedUser({ request, env, repo });
    if (request.method === 'POST') {
      const result = assetType === 'variant'
        ? await repo.subscribeVariant({ variantId: assetId, userId: user.userId })
        : await repo.subscribeComponent({ componentId: assetId, userId: user.userId });
      return jsonResponse(200, result);
    }
    if (request.method === 'DELETE') {
      const result = assetType === 'variant'
        ? await repo.unsubscribeVariant({ variantId: assetId, userId: user.userId })
        : await repo.unsubscribeComponent({ componentId: assetId, userId: user.userId });
      return jsonResponse(200, result);
    }
  }

  if (parts.length === 2 && parts[1] === 'fork' && request.method === 'POST') {
    const user = await requireAuthenticatedUser({ request, env, repo });
    const payload = await readJsonBody(request);
    const result = assetType === 'variant'
      ? await repo.forkVariant({ variantId: assetId, payload, user })
      : await repo.forkComponent({ componentId: assetId, payload, user });
    return jsonResponse(200, result);
  }

  return jsonResponse(404, { message: 'not found' });
}

async function routeAdminRequest({ request, url, repo, adminUser }) {
  if (request.method === 'GET' && url.pathname === '/v1/admin/users') {
    const users = await repo.listUsers({
      query: url.searchParams.get('q') || '',
      cursor: url.searchParams.get('cursor') || '',
    });
    return jsonResponse(200, users);
  }

  if (request.method === 'POST' && url.pathname === '/v1/admin/users') {
    const payload = await readJsonBody(request);
    const username = requireBodyString(payload.username, 'username is required');
    const password = requireBodyString(payload.password, 'password is required');
    const displayName = bodyStringOrDefault(payload.displayName, username);
    const passwordHash = await hashPassword(password);
    const user = await repo.createUser({
      username,
      passwordHash,
      displayName,
      isAdmin: Boolean(payload.isAdmin),
    });
    return jsonResponse(200, {
      userId: user.userId,
      username: user.username,
      displayName: user.displayName,
      isAdmin: user.isAdmin,
      createdAt: user.createdAt,
      updatedAt: user.updatedAt,
    });
  }

  if (request.method === 'GET' && url.pathname.startsWith('/v1/admin/users/')) {
    const userPath = '/v1/admin/users/';
    const tail = decodeURIComponent(url.pathname.slice(userPath.length));
    const parts = tail.split('/').filter((part) => part.length > 0);
    const userId = parts[0] || '';
    if (!userId) {
      return jsonResponse(404, { message: 'not found' });
    }
    if (parts.length === 1) {
      const user = await repo.getUserByIdWithStats(userId);
      if (user === null) {
        return jsonResponse(404, { message: 'user not found' });
      }
      return jsonResponse(200, user);
    }
    if (parts.length === 2 && parts[1] === 'assets') {
      const result = await repo.listAssetsByOwnerForAdmin({
        ownerUserId: userId,
        assetType: url.searchParams.get('assetType') || '',
        includeDeleted: url.searchParams.get('includeDeleted') || '',
        cursor: url.searchParams.get('cursor') || '',
      });
      return jsonResponse(200, result);
    }
  }

  if (request.method === 'PUT' && url.pathname.startsWith('/v1/admin/users/')) {
    const userId = decodeURIComponent(url.pathname.slice('/v1/admin/users/'.length));
    if (!userId || userId.includes('/')) {
      return jsonResponse(404, { message: 'not found' });
    }
    const payload = await readJsonBody(request);
    if (payload.password !== undefined) {
      const passwordHash = await hashPassword(requireBodyString(payload.password, 'password is required'));
      await repo.updateUserPassword({ userId, passwordHash });
      await repo.revokeRefreshTokensForUser(userId);
    }
    const updated = await repo.updateUserProfileByAdmin({
      userId,
      displayName: payload.displayName,
      isAdmin: payload.isAdmin,
    });
    if (updated === null) {
      return jsonResponse(404, { message: 'user not found' });
    }
    return jsonResponse(200, updated);
  }

  if (request.method === 'DELETE' && url.pathname.startsWith('/v1/admin/users/')) {
    const userId = decodeURIComponent(url.pathname.slice('/v1/admin/users/'.length));
    if (!userId || userId.includes('/')) {
      return jsonResponse(404, { message: 'not found' });
    }
    if (userId === adminUser.userId) {
      throw new HttpError(400, 'admin cannot delete self');
    }
    const deleted = await repo.deleteUserByAdmin(userId);
    if (!deleted) {
      return jsonResponse(404, { message: 'user not found' });
    }
    return jsonResponse(200, {});
  }

  if (request.method === 'GET' && url.pathname === '/v1/admin/assets') {
    const result = await repo.listAssetsForAdmin({
      assetType: url.searchParams.get('assetType') || '',
      ownerUserId: url.searchParams.get('ownerUserId') || '',
      query: url.searchParams.get('q') || '',
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      cursor: url.searchParams.get('cursor') || '',
    });
    return jsonResponse(200, result);
  }

  if (request.method === 'GET' && url.pathname.startsWith('/v1/admin/assets/')) {
    const assetId = decodeURIComponent(url.pathname.slice('/v1/admin/assets/'.length));
    if (!assetId || assetId.includes('/')) {
      return jsonResponse(404, { message: 'not found' });
    }
    const asset = await repo.getAssetForAdmin({
      assetId,
      includeDeleted: url.searchParams.get('includeDeleted') || '',
    });
    if (asset === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, asset);
  }

  if (request.method === 'PUT' && url.pathname.startsWith('/v1/admin/assets/')) {
    const assetId = decodeURIComponent(url.pathname.slice('/v1/admin/assets/'.length));
    if (!assetId || assetId.includes('/')) {
      return jsonResponse(404, { message: 'not found' });
    }
    const payload = await readJsonBody(request);
    if (payload.restore === true) {
      const restored = await repo.adminRestoreAsset({ assetId });
      if (!restored) {
        return jsonResponse(404, { message: 'asset not found' });
      }
    }
    if (payload.visibility !== undefined) {
      const updated = await repo.adminUpdateAssetVisibility({ assetId, visibility: payload.visibility });
      if (updated === null) {
        return jsonResponse(404, { message: 'asset not found' });
      }
      return jsonResponse(200, updated);
    }
    const current = await repo.getAssetForAdmin({ assetId, includeDeleted: true });
    if (current === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, current);
  }

  if (request.method === 'DELETE' && url.pathname.startsWith('/v1/admin/assets/')) {
    const assetId = decodeURIComponent(url.pathname.slice('/v1/admin/assets/'.length));
    if (!assetId || assetId.includes('/')) {
      return jsonResponse(404, { message: 'not found' });
    }
    const deleted = await repo.adminDeleteAsset({ assetId });
    if (!deleted) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, {});
  }

  return jsonResponse(404, { message: 'not found' });
}

async function ensureBootstrapUser(env, repo) {
  const username = String(env.BOOTSTRAP_ADMIN_USERNAME || '').trim();
  const password = String(env.BOOTSTRAP_ADMIN_PASSWORD || '').trim();
  if (!username || !password) {
    return;
  }
  const displayName = String(env.BOOTSTRAP_ADMIN_DISPLAY_NAME || 'Administrator').trim() || 'Administrator';
  const existing = await repo.findUserByUsername(username);
  if (existing !== null) {
    return;
  }
  const passwordHash = await hashPassword(password);
  await repo.ensureBootstrapUser({ username, passwordHash, displayName, isAdmin: true });
}

async function register({ env, repo, payload }) {
  const username = requireBodyString(payload.username, 'username is required');
  const password = requireBodyString(payload.password, 'password is required');
  const displayName = bodyStringOrDefault(payload.displayName, username);
  const passwordHash = await hashPassword(password);
  const user = await repo.createUser({ username, passwordHash, displayName, isAdmin: false });
  return issueAuthResponse({ env, repo, user });
}

async function login({ env, repo, username, password }) {
  const user = await repo.findUserByUsername(requireBodyString(username, 'username is required'));
  if (user === null) {
    throw new HttpError(401, 'invalid username or password');
  }
  const valid = await verifyPassword(String(password || ''), user.passwordHash);
  if (!valid) {
    throw new HttpError(401, 'invalid username or password');
  }
  return issueAuthResponse({ env, repo, user });
}

async function refreshAuth({ env, repo, refreshToken }) {
  let payload;
  try {
    payload = await verifyJwt({
      token: String(refreshToken || ''),
      secret: String(env.JWT_SECRET),
      issuer: issuer(env),
      expectedType: 'refresh',
    });
  } catch (error) {
    throw new HttpError(401, 'invalid refresh token');
  }
  const tokenId = String(payload.jti || '');
  const refreshRow = await repo.findActiveRefreshToken(tokenId);
  if (refreshRow === null) {
    throw new HttpError(401, 'refresh token revoked');
  }
  const user = await repo.findUserById(String(payload.sub || ''));
  if (user === null) {
    throw new HttpError(401, 'user not found');
  }
  return issueAuthResponse({ env, repo, user, refreshTokenId: refreshRow.tokenId });
}

async function changePassword({ repo, user, payload }) {
  const currentPassword = requireBodyString(payload.currentPassword, 'currentPassword is required');
  const newPassword = requireBodyString(payload.newPassword, 'newPassword is required');
  const valid = await verifyPassword(currentPassword, user.passwordHash);
  if (!valid) {
    throw new HttpError(401, 'invalid current password');
  }
  const passwordHash = await hashPassword(newPassword);
  await repo.updateUserPassword({ userId: user.userId, passwordHash });
  await repo.revokeRefreshTokensForUser(user.userId);
}

async function issueAuthResponse({ env, repo, user, refreshTokenId = null }) {
  let tokenId = refreshTokenId;
  if (!tokenId) {
    const refreshExpiry = futureIso(secondsFromEnv(env.REFRESH_TOKEN_TTL_SECONDS, 2592000));
    const refreshRow = await repo.issueRefreshToken({ userId: user.userId, expiresAt: refreshExpiry });
    tokenId = refreshRow.tokenId;
  }
  const tokens = await issueTokenPair({
    secret: String(env.JWT_SECRET),
    issuer: issuer(env),
    userId: user.userId,
    accessTtlSeconds: secondsFromEnv(env.ACCESS_TOKEN_TTL_SECONDS, 3600),
    refreshTtlSeconds: secondsFromEnv(env.REFRESH_TOKEN_TTL_SECONDS, 2592000),
    refreshTokenId: tokenId,
  });
  return {
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    user: {
      userId: user.userId,
      username: user.username,
      displayName: user.displayName,
      isAdmin: user.isAdmin,
    },
  };
}

async function requireAuthenticatedUser({ request, env, repo }) {
  const authHeader = request.headers.get('Authorization') || '';
  if (!authHeader.startsWith('Bearer ')) {
    throw new HttpError(401, 'missing bearer token');
  }
  const token = authHeader.slice('Bearer '.length).trim();
  let payload;
  try {
    payload = await verifyJwt({
      token,
      secret: String(env.JWT_SECRET),
      issuer: issuer(env),
      expectedType: 'access',
    });
  } catch (error) {
    throw new HttpError(401, 'invalid access token');
  }
  const user = await repo.findUserById(String(payload.sub || ''));
  if (user === null) {
    throw new HttpError(401, 'user not found');
  }
  return user;
}

async function requireAdminUser({ request, env, repo }) {
  const user = await requireAuthenticatedUser({ request, env, repo });
  if (!user.isAdmin) {
    throw new HttpError(403, 'admin only');
  }
  return user;
}

async function optionalAuthenticatedUser({ request, env, repo }) {
  const authHeader = request.headers.get('Authorization') || '';
  if (!authHeader.trim()) {
    return null;
  }
  return requireAuthenticatedUser({ request, env, repo });
}

function validateEnv(env) {
  if (!env || typeof env !== 'object' || env.DB === undefined || env.DB === null) {
    throw new HttpError(500, 'DB binding is not configured');
  }
  if (!String(env.JWT_SECRET || '').trim()) {
    throw new HttpError(500, 'JWT_SECRET is not configured');
  }
}

async function readJsonBody(request) {
  const raw = await request.text();
  if (!raw) {
    return {};
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new HttpError(400, 'request body must be a JSON object');
  }
  if (!isPlainObject(parsed)) {
    throw new HttpError(400, 'request body must be a JSON object');
  }
  return parsed;
}

function handleError(error) {
  if (error instanceof HttpError) {
    return jsonResponse(error.status, { message: error.message, ...error.payload });
  }
  if (error instanceof AssetPermissionError) {
    return jsonResponse(403, { message: error.message || 'forbidden' });
  }
  if (error instanceof AssetNotFoundError) {
    return jsonResponse(404, { message: 'asset not found' });
  }
  if (error instanceof AssetConflictError) {
    return jsonResponse(409, {
      message: 'conflict',
      assetId: error.assetId,
      revision: error.revision,
      remoteRevision: error.revision,
    });
  }
  if (error instanceof Error && error.message === 'username already exists') {
    return jsonResponse(409, { message: error.message });
  }
  if (error instanceof Error && error.message === 'cannot delete user with existing assets') {
    return jsonResponse(409, { message: error.message });
  }
  if (
    error instanceof Error &&
    (
      error.message.includes('required') ||
      error.message.includes('must be') ||
      error.message.includes('assetType') ||
      error.message.includes('visibility') ||
      error.message.includes('owner')
    )
  ) {
    return jsonResponse(400, { message: error.message });
  }
  if (error instanceof Error && error.message.endsWith('already exists')) {
    return jsonResponse(409, { message: error.message });
  }
  console.error('Unhandled unified asset worker error', error);
  return jsonResponse(500, { message: `internal error: ${error?.name || 'Error'}: ${error?.message || error}` });
}

function jsonResponse(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
      'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
    },
  });
}

function frontendFallbackResponse() {
  return new Response(buildAdminFallbackHtml(), {
    status: 200,
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

async function serveFrontend(request, env, url) {
  const assets = getAssetsBinding(env);
  if (assets === null) {
    return frontendFallbackResponse();
  }

  let assetPath = '/index.html';
  if (url.pathname.startsWith('/assets/')) {
    assetPath = url.pathname;
  } else if (url.pathname === '/favicon.ico') {
    assetPath = '/favicon.ico';
  } else {
    // All application routes are SPA routes resolved by index.html.
    assetPath = '/index.html';
  }

  const assetRequest = new Request(new URL(assetPath, request.url), request);
  return assets.fetch(assetRequest);
}

function getAssetsBinding(env) {
  if (!env || typeof env !== 'object') {
    return null;
  }
  const assets = env.ASSETS;
  if (assets && typeof assets.fetch === 'function') {
    return assets;
  }
  return null;
}

function buildAdminFallbackHtml() {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Feel8 Admin</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #0c1220; color: #edf2ff; }
      main { max-width: 760px; margin: 48px auto; padding: 24px; border: 1px solid #30456b; border-radius: 12px; background: #121c31; }
      h1 { margin-top: 0; }
      code { background: #0b1426; padding: 2px 6px; border-radius: 6px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Feel8 Admin</h1>
      <p>Frontend assets are not available yet.</p>
      <p>Build the Vite app first:</p>
      <p><code>npm run admin:build</code></p>
      <p>Then run Worker dev/deploy again.</p>
    </main>
  </body>
</html>`;
}

function futureIso(seconds) {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

function secondsFromEnv(value, fallback) {
  const parsed = Number.parseInt(String(value || fallback), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function issuer(env) {
  return String(env.JWT_ISSUER || 'feel8-asset-cloud');
}

function requireBodyString(value, message) {
  const text = String(value || '').trim();
  if (!text) {
    throw new HttpError(400, message);
  }
  return text;
}

function bodyStringOrDefault(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

class HttpError extends Error {
  constructor(status, message, payload = {}) {
    super(String(message || 'request failed'));
    this.status = status;
    this.payload = payload;
  }
}
