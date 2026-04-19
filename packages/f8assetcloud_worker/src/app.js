import { betterAuth, generateId } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { admin, username } from 'better-auth/plugins';
import { ApiException } from 'chanfana';
import { drizzle } from 'drizzle-orm/d1';
import { Hono } from 'hono';
import { cors } from 'hono/cors';

import { authSchema } from './auth_schema.js';
import { registerOpenApiRoutes } from './openapi.js';
import { authPasswordHashVersion, hashAuthPassword, verifyAuthPassword } from './password.js';
import { AssetConflictError, AssetNotFoundError, AssetPermissionError, AssetValidationError, AssetRepository } from './repository.js';
import { decompressGzip, isPlainObject, stringOrDefault, toBoolean } from './utils.js';

const AUTH_BASE_PATH = '/api/auth';
const CONSOLE_BASE_PATH = '/console';
const MANAGEMENT_API_BASE_PATH = '/v1/management';
const PURGE_ALL_ASSETS_CONFIRMATION_TEXT = 'DELETE ALL ASSETS';
const USER_ROLE_ADMIN = 'admin';
const USER_ROLE_USER = 'user';
const USER_ROLE_READONLY = 'readonly';
const BOOTSTRAP_ADMIN_STATE_ROW_ID = 1;
const RESERVED_IDENTITY_NAMES = new Set([
  'admin',
  'administrator',
  'owner',
  'root',
  'system',
  'feel8',
  'feel8fun',
  'f8',
  'f8studio',
  'support',
  'staff',
  'moderator',
  'official',
]);
const textEncoder = new TextEncoder();
let bootstrapAdminInitByDb = new WeakMap();
let authCacheByDb = new WeakMap();

export function createApp() {
  const app = new Hono();

  app.use('*', cors({
    origin: (origin, c) => resolveAllowedOrigin(c.env, origin),
    credentials: true,
    allowMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'HEAD'],
    allowHeaders: ['Content-Type'],
  }));

  app.all(`${AUTH_BASE_PATH}/*`, async (c) => {
    validateEnv(c.env);
    const auth = await getOrCreateAuth(c.env, c.req.raw);
    await ensureBootstrapAdmin({ env: c.env });
    if (await shouldBlockPublicRegistration({ db: c.env.DB, request: c.req.raw })) {
      return jsonResponse(403, { message: 'new user registration is disabled' });
    }
    return auth.handler(c.req.raw);
  });

  app.use('/v1/*', async (c, next) => {
    validateEnv(c.env);
    const auth = await getOrCreateAuth(c.env, c.req.raw);
    await ensureBootstrapAdmin({ env: c.env });
    c.set('auth', auth);
    c.set('repo', new AssetRepository(c.env.DB));
    await next();
  });

  registerOpenApiRoutes(app, {
    getAuthProviders: async (c) => ({
      google: hasGoogleProvider(c.env),
    }),
    getSiteSettings: async (c) => c.get('repo').getSiteSettings(),
    getMe: async (c) => {
      const user = await requireAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
      return toApiUser(user);
    },
    listComponents: async (c) => {
      const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
      return c.get('repo').listComponents({
        userId: viewer === null ? null : viewer.userId,
        query: c.req.query('q') || '',
        visibility: c.req.query('visibility') || '',
        owner: c.req.query('owner') || '',
        cursor: c.req.query('cursor') || '',
      });
    },
    getComponentContent: async (c) => {
      const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
      return c.get('repo').getComponentContent({
        componentId: c.req.param('componentId'),
        userId: viewer === null ? null : viewer.userId,
      });
    },
    listVariants: async (c) => {
      const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
      return c.get('repo').listVariants({
        userId: viewer === null ? null : viewer.userId,
        kind: c.req.query('kind') || '',
        baseNodeType: c.req.query('baseNodeType') || '',
        query: c.req.query('q') || '',
        visibility: c.req.query('visibility') || '',
        owner: c.req.query('owner') || '',
        cursor: c.req.query('cursor') || '',
      });
    },
    createVariant: async (c) => {
      const repo = c.get('repo');
      const user = await requireAssetWriteUser({ auth: c.get('auth'), repo, request: c.req.raw });
      const payload = await readJsonBody(c.req.raw);
      return repo.createVariant({ payload, user });
    },
    createComponent: async (c) => {
      const repo = c.get('repo');
      const user = await requireAssetWriteUser({ auth: c.get('auth'), repo, request: c.req.raw });
      const payload = await readJsonBody(c.req.raw);
      return repo.createComponent({ payload, user });
    },
    routeVariantAssetRequest: async (c) => routeAssetRequest({
      auth: c.get('auth'),
      repo: c.get('repo'),
      request: c.req.raw,
      url: new URL(c.req.raw.url),
      assetType: 'variant',
    }),
    routeComponentAssetRequest: async (c) => routeAssetRequest({
      auth: c.get('auth'),
      repo: c.get('repo'),
      request: c.req.raw,
      url: new URL(c.req.raw.url),
      assetType: 'component',
    }),
    routeManagementRequest: async (c) => {
      const managementUser = await requireManagementUser({ auth: c.get('auth'), request: c.req.raw });
      return routeManagementRequest({
        env: c.env,
        managementUser,
        auth: c.get('auth'),
        repo: c.get('repo'),
        request: c.req.raw,
        url: new URL(c.req.raw.url),
      });
    },
  });

  app.get('/v1/auth/verify-email', async (c) => {
    const auth = c.get('auth');
    const token = requireQueryString(c.req.query('token'), 'token is required');
    await auth.api.verifyEmail({
      query: { token },
      headers: c.req.raw.headers,
    });
    return c.json({ verified: true });
  });

  app.post('/v1/auth/reset-password', async (c) => {
    const auth = c.get('auth');
    const payload = await readJsonBody(c.req.raw);
    await auth.api.resetPassword({
      body: {
        token: requireBodyString(payload.token, 'token is required'),
        newPassword: requireBodyString(payload.newPassword, 'newPassword is required'),
      },
      headers: c.req.raw.headers,
    });
    return c.json({ reset: true });
  });

  app.post('/v1/me/password', async (c) => {
    const auth = c.get('auth');
    const payload = await readJsonBody(c.req.raw);
    await requireAuthenticatedUser({ auth, request: c.req.raw });
    await auth.api.changePassword({
      body: {
        currentPassword: requireBodyString(payload.currentPassword, 'currentPassword is required'),
        newPassword: requireBodyString(payload.newPassword, 'newPassword is required'),
        revokeOtherSessions: true,
      },
      headers: c.req.raw.headers,
    });
    return c.json({});
  });

  app.get('/v1/variants', async (c) => {
    const repo = c.get('repo');
    const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
    const result = await repo.listVariants({
      userId: viewer === null ? null : viewer.userId,
      kind: c.req.query('kind') || '',
      baseNodeType: c.req.query('baseNodeType') || '',
      query: c.req.query('q') || '',
      visibility: c.req.query('visibility') || '',
      owner: c.req.query('owner') || '',
      cursor: c.req.query('cursor') || '',
    });
    return c.json(result);
  });

  app.post('/v1/variants', async (c) => {
    const repo = c.get('repo');
    const user = await requireAssetWriteUser({ auth: c.get('auth'), repo, request: c.req.raw });
    const payload = await readJsonBody(c.req.raw);
    return c.json(await repo.createVariant({ payload, user }));
  });

  app.post('/v1/components', async (c) => {
    const repo = c.get('repo');
    const user = await requireAssetWriteUser({ auth: c.get('auth'), repo, request: c.req.raw });
    const payload = await readJsonBody(c.req.raw);
    return c.json(await repo.createComponent({ payload, user }));
  });

  app.all('/v1/variants/*', async (c) => routeAssetRequest({
    auth: c.get('auth'),
    repo: c.get('repo'),
    request: c.req.raw,
    url: new URL(c.req.raw.url),
    assetType: 'variant',
  }));

  app.all('/v1/components/*', async (c) => routeAssetRequest({
    auth: c.get('auth'),
    repo: c.get('repo'),
    request: c.req.raw,
    url: new URL(c.req.raw.url),
    assetType: 'component',
  }));

  app.all(`${MANAGEMENT_API_BASE_PATH}/*`, async (c) => {
    const managementUser = await requireManagementUser({ auth: c.get('auth'), request: c.req.raw });
    return routeManagementRequest({
      env: c.env,
      managementUser,
      auth: c.get('auth'),
      repo: c.get('repo'),
      request: c.req.raw,
      url: new URL(c.req.raw.url),
    });
  });

  app.on(['GET', 'HEAD'], '*', async (c) => serveFrontend(c.req.raw, c.env, new URL(c.req.raw.url)));

  app.all('*', () => jsonResponse(404, { message: 'not found' }));
  app.onError((error) => handleError(error));

  return app;
}

export function resetWorkerCachesForTesting() {
  bootstrapAdminInitByDb = new WeakMap();
  authCacheByDb = new WeakMap();
}

async function routeAssetRequest({ auth, repo, request, url, assetType }) {
  const prefix = assetType === 'variant' ? '/v1/variants/' : '/v1/components/';
  const tail = decodeURIComponent(url.pathname.slice(prefix.length));
  const parts = tail.split('/').filter((part) => part.length > 0);
  const assetId = parts[0] || '';
  if (!assetId) {
    return jsonResponse(404, { message: 'not found' });
  }

  if (parts.length === 1) {
    if (request.method === 'GET') {
      const viewer = await optionalAuthenticatedUser({ auth, request });
      const result = assetType === 'variant'
        ? await repo.getVariant({ variantId: assetId, userId: viewer === null ? null : viewer.userId })
        : await repo.getComponent({ componentId: assetId, userId: viewer === null ? null : viewer.userId });
      return jsonResponse(200, result);
    }
    if (request.method === 'PUT') {
      const user = await requireAssetWriteUser({ auth, repo, request });
      const payload = await readJsonBody(request);
      const result = assetType === 'variant'
        ? await repo.updateVariant({ variantId: assetId, payload, user })
        : await repo.updateComponent({ componentId: assetId, payload, user });
      return jsonResponse(200, result);
    }
    if (request.method === 'DELETE') {
      const user = await requireAssetWriteUser({ auth, repo, request });
      if (assetType === 'variant') {
        await repo.deleteVariant({ variantId: assetId, userId: user.userId });
      } else {
        await repo.deleteComponent({ componentId: assetId, userId: user.userId });
      }
      return jsonResponse(200, {});
    }
  }

  if (parts.length === 2 && parts[1] === 'content' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ auth, request });
    const result = assetType === 'variant'
      ? await repo.getVariantContent({ variantId: assetId, userId: viewer === null ? null : viewer.userId })
      : await repo.getComponentContent({ componentId: assetId, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 2 && parts[1] === 'visibility' && request.method === 'PUT') {
    const user = await requireAssetWriteUser({ auth, repo, request });
    const payload = await readJsonBody(request);
    const visibility = requireBodyString(payload.visibility, 'visibility is required');
    const result = assetType === 'variant'
      ? await repo.updateVariantVisibility({ variantId: assetId, visibility, revision: payload.revision, userId: user.userId })
      : await repo.updateComponentVisibility({ componentId: assetId, visibility, revision: payload.revision, userId: user.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 2 && parts[1] === 'versions' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ auth, request });
    const result = assetType === 'variant'
      ? await repo.listVariantVersions({ variantId: assetId, userId: viewer === null ? null : viewer.userId })
      : await repo.listComponentVersions({ componentId: assetId, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 3 && parts[1] === 'versions' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ auth, request });
    const versionNumber = parts[2];
    const result = assetType === 'variant'
      ? await repo.getVariantVersion({ variantId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId })
      : await repo.getComponentVersion({ componentId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 4 && parts[1] === 'versions' && parts[3] === 'content' && request.method === 'GET') {
    const viewer = await optionalAuthenticatedUser({ auth, request });
    const versionNumber = parts[2];
    const result = assetType === 'variant'
      ? await repo.getVariantVersionContent({ variantId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId })
      : await repo.getComponentVersionContent({ componentId: assetId, versionNumber, userId: viewer === null ? null : viewer.userId });
    return jsonResponse(200, result);
  }

  if (parts.length === 2 && parts[1] === 'subscribe') {
    const user = await requireAuthenticatedUser({ auth, request });
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
    const user = await requireAssetWriteUser({ auth, repo, request });
    const payload = await readJsonBody(request);
    const result = assetType === 'variant'
      ? await repo.forkVariant({ variantId: assetId, payload, user })
      : await repo.forkComponent({ componentId: assetId, payload, user });
    return jsonResponse(200, result);
  }

  return jsonResponse(404, { message: 'not found' });
}

async function routeManagementRequest({ env, managementUser, auth, repo, request, url }) {
  if (request.method === 'GET' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/users`) {
    const users = await repo.listUsers({
      query: url.searchParams.get('q') || '',
      cursor: url.searchParams.get('cursor') || '',
    });
    return jsonResponse(200, users);
  }

  if (request.method === 'POST' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/users`) {
    const payload = await readJsonBody(request);
    const usernameValue = requireBodyString(payload.username, 'username is required');
    const displayName = bodyStringOrDefault(payload.displayName, usernameValue);
    const role = normalizeManagedUserRolePayload(payload);
    const created = await auth.api.createUser({
      body: {
        email: requireBodyString(payload.email, 'email is required'),
        password: requireBodyString(payload.password, 'password is required'),
        name: displayName,
        role,
        data: {
          username: normalizeUsername(usernameValue),
          displayUsername: displayName,
        },
      },
      headers: request.headers,
    });
    const user = await repo.getUserByIdWithStats(String(created.user.id));
    return jsonResponse(200, user ?? toApiUser(toAppUser(created.user)));
  }

  if (request.method === 'GET' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/site-settings`) {
    return jsonResponse(200, await repo.getSiteSettings());
  }

  if (request.method === 'GET' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/users/`)) {
    const parts = parseManagementUserPath(url.pathname);
    const userId = parts.userId;
    if (!userId) {
      return jsonResponse(404, { message: 'not found' });
    }
    if (parts.suffix.length === 0) {
      const user = await repo.getUserByIdWithStats(userId);
      if (user === null) {
        return jsonResponse(404, { message: 'user not found' });
      }
      return jsonResponse(200, user);
    }
  }

  if (request.method === 'PUT' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/users/`)) {
    const parts = parseManagementUserPath(url.pathname);
    if (!parts.userId || parts.suffix.length !== 0) {
      return jsonResponse(404, { message: 'not found' });
    }
    const payload = await readJsonBody(request);
    const data = {};
    if (payload.username !== undefined) {
      data.username = normalizeUsername(requireBodyString(payload.username, 'username is required'));
    }
    if (payload.displayName !== undefined) {
      const displayName = requireBodyString(payload.displayName, 'displayName is required');
      data.name = displayName;
      data.displayUsername = displayName;
    }
    if (Object.keys(data).length > 0) {
      await auth.api.adminUpdateUser({
        body: {
          userId: parts.userId,
          data,
        },
        headers: request.headers,
      });
    }
    if (payload.role !== undefined || payload.isAdmin !== undefined || payload.canUpload !== undefined) {
      await auth.api.setRole({
        body: {
          userId: parts.userId,
          role: normalizeManagedUserRolePayload(payload),
        },
        headers: request.headers,
      });
    }
    if (payload.password !== undefined) {
      await auth.api.setUserPassword({
        body: {
          userId: parts.userId,
          newPassword: requireBodyString(payload.password, 'password is required'),
        },
        headers: request.headers,
      });
    }
    const updated = await repo.getUserByIdWithStats(parts.userId);
    if (updated === null) {
      return jsonResponse(404, { message: 'user not found' });
    }
    return jsonResponse(200, updated);
  }

  if (request.method === 'PUT' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/site-settings`) {
    const payload = await readJsonBody(request);
    const updated = await repo.updateSiteSettings({
      allowUserRegistration: payload.allowUserRegistration,
      updatedByUserId: managementUser.userId,
    });
    invalidateAuthCache(env?.DB);
    return jsonResponse(200, updated);
  }

  if (request.method === 'POST' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/assets/purge-all`) {
    const payload = await readJsonBody(request);
    const confirmationText = requireBodyString(payload.confirmationText, 'confirmationText is required');
    if (confirmationText !== PURGE_ALL_ASSETS_CONFIRMATION_TEXT) {
      throw new HttpError(400, `confirmationText must equal ${PURGE_ALL_ASSETS_CONFIRMATION_TEXT}`);
    }
    return jsonResponse(200, await repo.adminPurgeAllAssets());
  }

  if (request.method === 'DELETE' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/users/`)) {
    const parts = parseManagementUserPath(url.pathname);
    if (!parts.userId || parts.suffix.length !== 0) {
      return jsonResponse(404, { message: 'not found' });
    }
    if (parts.userId === managementUser.userId) {
      throw new HttpError(400, 'management user cannot delete self');
    }
    await assertUserHasNoAssets(repo, parts.userId);
    await auth.api.removeUser({
      body: { userId: parts.userId },
      headers: request.headers,
    });
    return jsonResponse(200, {});
  }

  if (request.method === 'GET' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/components`) {
    const result = await repo.listManagedAssets({
      assetType: 'component',
      ownerUserId: url.searchParams.get('ownerUserId') || '',
      query: url.searchParams.get('q') || '',
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      cursor: url.searchParams.get('cursor') || '',
    });
    return jsonResponse(200, result);
  }

  if (request.method === 'GET' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/variants`) {
    const result = await repo.listManagedAssets({
      assetType: 'variant',
      ownerUserId: url.searchParams.get('ownerUserId') || '',
      query: url.searchParams.get('q') || '',
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      cursor: url.searchParams.get('cursor') || '',
      kind: url.searchParams.get('kind') || '',
      baseNodeType: url.searchParams.get('baseNodeType') || '',
    });
    return jsonResponse(200, result);
  }

  if (request.method === 'GET' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/components/`)) {
    const componentId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/components/`);
    if (!componentId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const asset = await repo.getManagedAsset({
      assetId: componentId,
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      assetTypeHint: 'component',
    });
    if (asset === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, asset);
  }

  if (request.method === 'GET' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/variants/`)) {
    const variantId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/variants/`);
    if (!variantId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const asset = await repo.getManagedAsset({
      assetId: variantId,
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      assetTypeHint: 'variant',
    });
    if (asset === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, asset);
  }

  if (request.method === 'PUT' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/components/`)) {
    const componentId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/components/`);
    if (!componentId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const payload = await readJsonBody(request);
    if (payload.restore === true) {
      const restored = await repo.adminRestoreAsset({ assetId: componentId, assetTypeHint: 'component' });
      if (!restored) {
        return jsonResponse(404, { message: 'asset not found' });
      }
    }
    if (payload.visibility !== undefined) {
      const updated = await repo.adminUpdateAssetVisibility({
        assetId: componentId,
        visibility: payload.visibility,
        assetTypeHint: 'component',
      });
      if (updated === null) {
        return jsonResponse(404, { message: 'asset not found' });
      }
      return jsonResponse(200, updated);
    }
    const current = await repo.getManagedAsset({ assetId: componentId, includeDeleted: true, assetTypeHint: 'component' });
    if (current === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, current);
  }

  if (request.method === 'PUT' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/variants/`)) {
    const variantId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/variants/`);
    if (!variantId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const payload = await readJsonBody(request);
    if (payload.restore === true) {
      const restored = await repo.adminRestoreAsset({ assetId: variantId, assetTypeHint: 'variant' });
      if (!restored) {
        return jsonResponse(404, { message: 'asset not found' });
      }
    }
    if (payload.visibility !== undefined) {
      const updated = await repo.adminUpdateAssetVisibility({
        assetId: variantId,
        visibility: payload.visibility,
        assetTypeHint: 'variant',
      });
      if (updated === null) {
        return jsonResponse(404, { message: 'asset not found' });
      }
      return jsonResponse(200, updated);
    }
    const current = await repo.getManagedAsset({ assetId: variantId, includeDeleted: true, assetTypeHint: 'variant' });
    if (current === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, current);
  }

  if (request.method === 'DELETE' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/components/`)) {
    const componentId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/components/`);
    if (!componentId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const deleted = await repo.adminDeleteAsset({ assetId: componentId, assetTypeHint: 'component' });
    if (!deleted) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, {});
  }

  if (request.method === 'DELETE' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/variants/`)) {
    const variantId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/variants/`);
    if (!variantId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const deleted = await repo.adminDeleteAsset({ assetId: variantId, assetTypeHint: 'variant' });
    if (!deleted) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, {});
  }

  return jsonResponse(404, { message: 'not found' });
}

function createAuth(env, { siteSettings, baseURL, trustedOrigins }) {
  const db = drizzle(env.DB);
  const bootstrapUsername = String(env.BOOTSTRAP_ADMIN_USERNAME || '').trim().toLowerCase();
  const bootstrapDisplayName = String(env.BOOTSTRAP_ADMIN_DISPLAY_NAME || '').trim();
  const socialProviders = {};
  if (hasGoogleProvider(env)) {
    socialProviders.google = {
      clientId: String(env.GOOGLE_CLIENT_ID || '').trim(),
      clientSecret: String(env.GOOGLE_CLIENT_SECRET || '').trim(),
      disableImplicitSignUp: !siteSettings.allowUserRegistration,
    };
  }

  return betterAuth({
    secret: getAuthSecret(env),
    baseURL,
    basePath: AUTH_BASE_PATH,
    trustedOrigins,
    database: drizzleAdapter(db, {
      provider: 'sqlite',
      schema: authSchema,
      usePlural: false,
      transaction: false,
    }),
    emailAndPassword: {
      enabled: true,
      autoSignIn: false,
      requireEmailVerification: true,
      revokeSessionsOnPasswordReset: true,
      password: {
        hash: hashAuthPassword,
        verify: verifyAuthPassword,
      },
      resetPasswordTokenExpiresIn: secondsFromEnv(env.PASSWORD_RESET_TOKEN_TTL_SECONDS, 1800),
      sendResetPassword: async ({ user, token, url }) => {
        const appUser = toAppUser(user);
        const resetUrl = resolveAuthActionUrl({
          preferredUrl: url,
          fallbackUrl: buildResetPasswordUrl({ env, baseURL, token }),
        });
        await sendResetPasswordMessage({
          env,
          toEmail: appUser.email,
          username: appUser.displayName || appUser.username,
          resetUrl,
        });
      },
    },
    emailVerification: {
      sendOnSignIn: true,
      sendOnSignUp: true,
      autoSignInAfterVerification: false,
      expiresIn: secondsFromEnv(env.EMAIL_VERIFY_TOKEN_TTL_SECONDS, 1800),
      sendVerificationEmail: async ({ user, token, url }) => {
        const appUser = toAppUser(user);
        const verificationUrl = resolveAuthActionUrl({
          preferredUrl: url,
          fallbackUrl: buildVerifyEmailUrl({ env, baseURL, token }),
        });
        await sendVerifyEmailMessage({
          env,
          toEmail: appUser.email,
          username: appUser.displayName || appUser.username,
          verificationUrl,
        });
      },
    },
    socialProviders,
    plugins: [
      username({
        usernameNormalization: (value) => String(value || '').trim().toLowerCase(),
        usernameValidator: (value) => validateUsername(value, bootstrapUsername),
        displayUsernameValidator: (value) => validateDisplayName(value, bootstrapDisplayName),
      }),
      admin({
        defaultRole: USER_ROLE_USER,
        adminRoles: [USER_ROLE_ADMIN],
      }),
    ],
  });
}

async function getOrCreateAuth(env, request) {
  const siteSettings = await readSiteSettings(env.DB);
  const requestUrl = new URL(request.url);
  const baseURL = resolveAuthBaseUrl(env, requestUrl);
  const trustedOrigins = resolveTrustedOrigins(env, requestUrl, baseURL);
  const cacheKey = buildAuthCacheKey({
    allowUserRegistration: siteSettings.allowUserRegistration,
    baseURL,
    trustedOrigins,
  });
  const dbCache = getOrCreateDbCache(authCacheByDb, env.DB);
  const cached = dbCache.get(cacheKey);
  if (cached !== undefined) {
    return cached;
  }
  const auth = createAuth(env, {
    siteSettings,
    baseURL,
    trustedOrigins,
  });
  dbCache.set(cacheKey, auth);
  return auth;
}

async function ensureBootstrapAdmin({ env }) {
  const config = readBootstrapAdminConfig(env);
  if (config === null) {
    return;
  }

  const db = env?.DB;
  if (db && typeof db === 'object') {
    const pending = bootstrapAdminInitByDb.get(db);
    if (pending) {
      await pending;
      return;
    }

    const initPromise = ensureBootstrapAdminOnce(env, config);
    bootstrapAdminInitByDb.set(db, initPromise);
    try {
      await initPromise;
    } catch (error) {
      bootstrapAdminInitByDb.delete(db);
      throw error;
    }
    return;
  }

  await ensureBootstrapAdminOnce(env, config);
}

function readBootstrapAdminConfig(env) {
  const usernameValue = String(env.BOOTSTRAP_ADMIN_USERNAME || '').trim().toLowerCase();
  const password = String(env.BOOTSTRAP_ADMIN_PASSWORD || '').trim();
  if (!usernameValue || !password) {
    return null;
  }
  return {
    usernameValue,
    password,
    email: String(env.BOOTSTRAP_ADMIN_EMAIL || `${usernameValue}@local.invalid`).trim().toLowerCase(),
    displayName: String(env.BOOTSTRAP_ADMIN_DISPLAY_NAME || 'Administrator').trim() || 'Administrator',
  };
}

async function ensureBootstrapAdminOnce(env, config) {
  const configFingerprint = await computeBootstrapAdminFingerprint(env, config);
  const syncedState = await readBootstrapAdminState(env.DB);
  if (syncedState !== null && syncedState.configFingerprint === configFingerprint) {
    const syncedAdmin = await readBootstrapAdminCredentialAccount(env.DB, syncedState.userId);
    if (bootstrapAdminMatchesConfig(syncedAdmin, config)) {
      return;
    }
  }

  const existing = await env.DB.prepare(
    `SELECT
       u.id,
       a.id AS credential_account_id
     FROM user u
     LEFT JOIN account a
       ON a.userId = u.id AND a.providerId = 'credential'
     WHERE u.email = ? OR u.username = ?
     LIMIT 1`,
  )
    .bind(config.email, config.usernameValue)
    .first();
  const timestamp = Date.now();

  if (existing === null) {
    const passwordHash = await hashAuthPassword(config.password);
    const userId = generateId();
    await env.DB.prepare(
      `INSERT INTO user
         ("id", "name", "email", "emailVerified", "image", "createdAt", "updatedAt", "username", "displayUsername", "role", "banned", "banReason", "banExpires")
       VALUES (?, ?, ?, 1, NULL, ?, ?, ?, ?, 'admin', 0, NULL, NULL)`,
    )
      .bind(userId, config.displayName, config.email, timestamp, timestamp, config.usernameValue, config.displayName)
      .run();
    await env.DB.prepare(
      `INSERT INTO account
         ("id", "accountId", "providerId", "userId", "accessToken", "refreshToken", "idToken", "accessTokenExpiresAt", "refreshTokenExpiresAt", "scope", "password", "createdAt", "updatedAt")
       VALUES (?, ?, 'credential', ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)`,
    )
      .bind(generateId(), userId, userId, passwordHash, timestamp, timestamp)
      .run();
    await upsertBootstrapAdminState(env.DB, {
      configFingerprint,
      userId,
    });
    return;
  }

  const userId = String(existing.id);
  await env.DB.prepare(
    `UPDATE user
     SET email = ?,
         role = 'admin',
         emailVerified = 1,
         username = ?,
         displayUsername = ?,
         name = ?,
         updatedAt = ?
     WHERE id = ?`,
  )
    .bind(config.email, config.usernameValue, config.displayName, config.displayName, timestamp, userId)
    .run();

  if (existing.credential_account_id === null || existing.credential_account_id === undefined) {
    const passwordHash = await hashAuthPassword(config.password);
    await env.DB.prepare(
      `INSERT INTO account
         ("id", "accountId", "providerId", "userId", "accessToken", "refreshToken", "idToken", "accessTokenExpiresAt", "refreshTokenExpiresAt", "scope", "password", "createdAt", "updatedAt")
       VALUES (?, ?, 'credential', ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)`,
    )
      .bind(generateId(), userId, userId, passwordHash, timestamp, timestamp)
      .run();
    await upsertBootstrapAdminState(env.DB, {
      configFingerprint,
      userId,
    });
    return;
  }

  if (syncedState === null || syncedState.configFingerprint !== configFingerprint) {
    const passwordHash = await hashAuthPassword(config.password);
    await env.DB.prepare(
      `UPDATE account
       SET accountId = ?,
           password = ?,
           updatedAt = ?
       WHERE id = ?`,
    )
      .bind(userId, passwordHash, timestamp, String(existing.credential_account_id))
      .run();
  }

  await upsertBootstrapAdminState(env.DB, {
    configFingerprint,
    userId,
  });
}

async function requireAuthenticatedUser({ auth, request }) {
  const session = await auth.api.getSession({
    headers: request.headers,
  });
  if (session === null) {
    throw new HttpError(401, 'authentication required');
  }
  return toAppUser(session.user);
}

async function optionalAuthenticatedUser({ auth, request }) {
  const cookie = request.headers.get('cookie') || '';
  if (!cookie.trim()) {
    return null;
  }
  const session = await auth.api.getSession({
    headers: request.headers,
  });
  return session === null ? null : toAppUser(session.user);
}

async function requireManagementUser({ auth, request }) {
  const user = await requireAuthenticatedUser({ auth, request });
  if (!user.isAdmin) {
    throw new HttpError(403, 'management access required');
  }
  return user;
}

async function requireAssetWriteUser({ auth, repo, request }) {
  const user = await requireAuthenticatedUser({ auth, request });
  const latestUser = await repo.getUserByIdWithStats(user.userId);
  if (latestUser === null) {
    throw new HttpError(401, 'authentication required');
  }
  if (latestUser.role === USER_ROLE_READONLY) {
    throw new HttpError(403, 'upload permission required');
  }
  return {
    ...user,
    role: latestUser.role,
    canUpload: latestUser.canUpload,
  };
}

async function assertUserHasNoAssets(repo, userId) {
  const ownsAssets = await repo.hasAssets(userId);
  if (ownsAssets) {
    throw new HttpError(409, 'cannot delete user with existing assets');
  }
}

function toApiUser(user) {
  return {
    userId: user.userId,
    username: user.username,
    displayName: user.displayName,
    email: user.email,
    emailVerified: user.emailVerified,
    isAdmin: user.isAdmin,
    role: user.role,
    canUpload: user.canUpload,
  };
}

function toAppUser(user) {
  const email = stringOrDefault(user.email, '');
  const usernameValue = stringOrDefault(user.username, email || String(user.id || ''));
  const displayName = stringOrDefault(user.displayUsername, stringOrDefault(user.name, usernameValue));
  return {
    userId: String(user.id),
    username: usernameValue,
    displayName,
    email,
    emailVerified: Boolean(user.emailVerified),
    role: normalizeUserRole(user.role),
    isAdmin: normalizeUserRole(user.role) === USER_ROLE_ADMIN,
    canUpload: normalizeUserRole(user.role) !== USER_ROLE_READONLY,
  };
}

function validateEnv(env) {
  if (!env || typeof env !== 'object' || env.DB === undefined || env.DB === null) {
    throw new HttpError(500, 'DB binding is not configured');
  }
  if (!getAuthSecret(env)) {
    throw new HttpError(500, 'BETTER_AUTH_SECRET is not configured');
  }
}

function getAuthSecret(env) {
  return String(env.BETTER_AUTH_SECRET || '').trim();
}

function hasGoogleProvider(env) {
  return Boolean(String(env.GOOGLE_CLIENT_ID || '').trim() && String(env.GOOGLE_CLIENT_SECRET || '').trim());
}

function getOrCreateDbCache(cacheByDb, db) {
  let cache = cacheByDb.get(db);
  if (cache !== undefined) {
    return cache;
  }
  cache = new Map();
  cacheByDb.set(db, cache);
  return cache;
}

function buildAuthCacheKey({ allowUserRegistration, baseURL, trustedOrigins }) {
  return JSON.stringify({
    allowUserRegistration: Boolean(allowUserRegistration),
    baseURL: String(baseURL || '').trim(),
    trustedOrigins: [...trustedOrigins].sort(),
  });
}

function invalidateAuthCache(db) {
  if (db && typeof db === 'object') {
    authCacheByDb.delete(db);
  }
}

function resolveAuthBaseUrl(env, requestUrl) {
  const configured = String(env.AUTH_BASE_URL || '').trim();
  if (configured) {
    return configured;
  }
  return requestUrl.origin;
}

function resolveTrustedOrigins(env, requestUrl, baseURL) {
  const origins = new Set();
  addOrigin(origins, baseURL);
  addOrigin(origins, requestUrl.origin);
  const extra = String(env.CORS_ALLOWED_ORIGINS || '').trim();
  if (extra) {
    for (const value of extra.split(',')) {
      addOrigin(origins, value);
    }
  }
  return [...origins];
}

function addOrigin(target, value) {
  const text = String(value || '').trim();
  if (!text) {
    return;
  }
  try {
    target.add(new URL(text).origin);
  } catch (error) {
    console.warn('Ignoring invalid trusted origin', text);
  }
}

function resolveAllowedOrigin(env, origin) {
  const baseUrl = String(env.AUTH_BASE_URL || '').trim();
  if (!baseUrl) {
    return origin || '*';
  }
  const primaryOrigin = new URL(baseUrl).origin;
  if (!origin || origin === primaryOrigin) {
    return primaryOrigin;
  }
  const extra = String(env.CORS_ALLOWED_ORIGINS || '').trim();
  if (extra) {
    for (const allowed of extra.split(',')) {
      if (allowed.trim() === origin) {
        return origin;
      }
    }
  }
  return primaryOrigin;
}

function validateIdentityName(value) {
  const text = String(value || '').trim();
  if (!text) {
    return false;
  }
  const canonical = canonicalizeIdentityName(text);
  if (RESERVED_IDENTITY_NAMES.has(canonical)) {
    return false;
  }
  return isSafeDisplayText(text);
}

function validateUsername(value, allowedReservedValue = '') {
  const text = String(value || '').trim();
  if (!text) {
    return false;
  }
  const canonical = canonicalizeIdentityName(text);
  if (RESERVED_IDENTITY_NAMES.has(canonical) && canonical !== canonicalizeIdentityName(allowedReservedValue)) {
    return false;
  }
  return /^[A-Za-z0-9_]{3,64}$/.test(text);
}

function validateDisplayName(value, allowedReservedValue = '') {
  const text = String(value || '').trim();
  if (!text) {
    return false;
  }
  const canonical = canonicalizeIdentityName(text);
  if (RESERVED_IDENTITY_NAMES.has(canonical) && canonical !== canonicalizeIdentityName(allowedReservedValue)) {
    return false;
  }
  return isSafeDisplayText(text);
}

function isSafeDisplayText(value) {
  const text = String(value || '').trim();
  if (text.length < 2 || text.length > 64) {
    return false;
  }
  if (/[\u0000-\u001F\u007F]/.test(text)) {
    return false;
  }
  return true;
}

function canonicalizeIdentityName(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[\s._-]+/g, '');
}

function parseManagementUserPath(pathname) {
  const prefix = `${MANAGEMENT_API_BASE_PATH}/users/`;
  const tail = decodeURIComponent(pathname.slice(prefix.length));
  const parts = tail.split('/').filter((part) => part.length > 0);
  return {
    userId: parts[0] || '',
    suffix: parts.slice(1),
  };
}

function decodeSinglePathValue(pathname, prefix) {
  const value = decodeURIComponent(pathname.slice(prefix.length));
  if (!value || value.includes('/')) {
    return '';
  }
  return value;
}

async function readJsonBody(request) {
  const contentEncoding = String(request.headers.get('Content-Encoding') || '').toLowerCase();
  let raw = '';
  if (contentEncoding.includes('gzip')) {
    const compressedBody = await request.arrayBuffer();
    if (compressedBody.byteLength === 0) {
      return {};
    }
    try {
      raw = await decompressGzip(new Uint8Array(compressedBody));
    } catch (error) {
      throw new HttpError(400, 'request body gzip decompression failed');
    }
  } else {
    raw = await request.text();
  }
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
  if (error instanceof ApiException) {
    const errors = error.buildResponse();
    const primaryMessage = errors.length > 0 ? String(errors[0].message || '') : String(error.message || '');
    return jsonResponse(error.status, {
      message: primaryMessage || 'request failed',
      errors,
    });
  }
  const apiError = toAuthApiErrorPayload(error);
  if (apiError !== null) {
    return jsonResponse(apiError.status, apiError.payload);
  }
  if (error instanceof AssetPermissionError) {
    return jsonResponse(403, { message: 'forbidden' });
  }
  if (error instanceof AssetNotFoundError) {
    return jsonResponse(404, { message: 'asset not found' });
  }
  if (error instanceof AssetConflictError) {
    const payload = {
      message: 'conflict',
      revision: error.revision,
      remoteRevision: error.revision,
    };
    if (error.assetType === 'variant') {
      payload.variantId = error.assetId;
    } else {
      payload.componentId = error.assetId;
    }
    return jsonResponse(409, payload);
  }
  if (error instanceof AssetValidationError) {
    return jsonResponse(400, { message: error.message });
  }
  if (error instanceof Error && isUniqueConstraintError(error)) {
    return jsonResponse(409, { message: 'duplicate resource' });
  }
  if (error instanceof Error && error.message) {
    if (error.message.includes('already exists') || error.message.includes('duplicate')) {
      return jsonResponse(409, { message: 'duplicate resource' });
    }
  }
  console.error('Unhandled asset worker error', error);
  return jsonResponse(500, { message: 'internal error' });
}

function toAuthApiErrorPayload(error) {
  const status = Number(error?.statusCode || 0);
  if (!Number.isInteger(status) || status < 400 || status > 599) {
    return null;
  }
  const body = isPlainObject(error?.body) ? error.body : {};
  const message = body.message ? String(body.message) : (error instanceof Error ? error.message : 'request failed');
  const payload = {
    ...body,
    message,
  };
  return { status, payload };
}

function isUniqueConstraintError(error) {
  const message = error instanceof Error ? error.message : '';
  const causeMessage = error?.cause instanceof Error ? error.cause.message : '';
  const causeErrcode = Number(error?.cause?.errcode || 0);
  return (
    message.includes('UNIQUE constraint failed')
    || causeMessage.includes('UNIQUE constraint failed')
    || causeErrcode === 2067
  );
}

function jsonResponse(status, payload) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  });
}

function frontendFallbackResponse() {
  return new Response(buildConsoleFallbackHtml(), {
    status: 200,
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

function buildVerifyEmailUrl({ env, baseURL, token }) {
  const configuredBaseUrl = String(env.AUTH_VERIFY_EMAIL_BASE_URL || '').trim();
  const base = configuredBaseUrl
    ? new URL(configuredBaseUrl)
    : new URL(`${CONSOLE_BASE_PATH}/verify-email`, baseURL);
  base.searchParams.set('token', token);
  return base.toString();
}

function buildResetPasswordUrl({ env, baseURL, token }) {
  const configuredBaseUrl = String(env.AUTH_RESET_PASSWORD_BASE_URL || '').trim();
  const base = configuredBaseUrl
    ? new URL(configuredBaseUrl)
    : new URL(`${CONSOLE_BASE_PATH}/reset-password`, baseURL);
  base.searchParams.set('token', token);
  return base.toString();
}

function resolveAuthActionUrl({ preferredUrl, fallbackUrl }) {
  const candidate = String(preferredUrl || '').trim();
  if (candidate) {
    return candidate;
  }
  return String(fallbackUrl || '').trim();
}

async function sendVerifyEmailMessage({ env, toEmail, username, verificationUrl }) {
  await sendAuthEmail({
    env,
    debugLabel: 'verify email',
    debugUrl: verificationUrl,
    toEmail,
    subject: 'Verify your email',
    text: `Hi ${username}, verify your email: ${verificationUrl}`,
    html: `<p>Hi ${escapeHtml(username)},</p><p>Please verify your email:</p><p><a href="${escapeHtml(verificationUrl)}">${escapeHtml(verificationUrl)}</a></p>`,
  });
}

async function sendResetPasswordMessage({ env, toEmail, username, resetUrl }) {
  await sendAuthEmail({
    env,
    debugLabel: 'reset password',
    debugUrl: resetUrl,
    toEmail,
    subject: 'Reset your password',
    text: `Hi ${username}, reset your password: ${resetUrl}`,
    html: `<p>Hi ${escapeHtml(username)},</p><p>Use this link to reset password:</p><p><a href="${escapeHtml(resetUrl)}">${escapeHtml(resetUrl)}</a></p>`,
  });
}

async function shouldBlockPublicRegistration({ db, request }) {
  if (!isPublicRegistrationRequest(request)) {
    return false;
  }
  const settings = await readSiteSettings(db);
  return !settings.allowUserRegistration;
}

function isPublicRegistrationRequest(request) {
  const url = new URL(request.url);
  if (request.method !== 'POST') {
    return false;
  }
  return url.pathname === `${AUTH_BASE_PATH}/sign-up/email`;
}

async function readSiteSettings(db) {
  const row = await db.prepare(
    `SELECT allow_user_registration
     FROM site_settings
     WHERE id = 1`,
  ).first();
  if (row === null) {
    await db.prepare(
      `INSERT INTO site_settings (id, allow_user_registration, updated_at, updated_by_user_id)
       VALUES (1, 0, CURRENT_TIMESTAMP, NULL)
       ON CONFLICT(id) DO NOTHING`,
    ).run();
    return {
      allowUserRegistration: false,
    };
  }
  return {
    allowUserRegistration: Number(row?.allow_user_registration ?? 0) !== 0,
  };
}

async function computeBootstrapAdminFingerprint(env, config) {
  const payload = JSON.stringify({
    authSecret: getAuthSecret(env),
    passwordHashVersion: authPasswordHashVersion(),
    email: config.email,
    password: config.password,
    displayName: config.displayName,
    usernameValue: config.usernameValue,
  });
  const digest = await crypto.subtle.digest('SHA-256', textEncoder.encode(payload));
  return bytesToHex(new Uint8Array(digest));
}

async function readBootstrapAdminState(db) {
  const row = await db.prepare(
    `SELECT config_fingerprint, user_id
     FROM bootstrap_admin_state
     WHERE id = ?`,
  )
    .bind(BOOTSTRAP_ADMIN_STATE_ROW_ID)
    .first();
  if (row === null) {
    return null;
  }
  const configFingerprint = String(row.config_fingerprint || '').trim();
  const userId = String(row.user_id || '').trim();
  if (!configFingerprint || !userId) {
    return null;
  }
  return {
    configFingerprint,
    userId,
  };
}

async function readBootstrapAdminCredentialAccount(db, userId) {
  return db.prepare(
    `SELECT
       u.id,
       u.email,
       u.username,
       u.displayUsername,
       u.name,
       u.role,
       u.emailVerified,
       a.id AS credential_account_id
     FROM user u
     LEFT JOIN account a
       ON a.userId = u.id AND a.providerId = 'credential'
     WHERE u.id = ?
     LIMIT 1`,
  )
    .bind(String(userId))
    .first();
}

function bootstrapAdminMatchesConfig(row, config) {
  if (row === null) {
    return false;
  }
  if (row.credential_account_id === null || row.credential_account_id === undefined) {
    return false;
  }
  return (
    String(row.email || '').trim().toLowerCase() === config.email
    && String(row.username || '').trim().toLowerCase() === config.usernameValue
    && String(row.displayUsername || '').trim() === config.displayName
    && String(row.name || '').trim() === config.displayName
    && String(row.role || '').trim() === USER_ROLE_ADMIN
    && Number(row.emailVerified || 0) !== 0
  );
}

async function upsertBootstrapAdminState(db, { configFingerprint, userId }) {
  await db.prepare(
    `INSERT INTO bootstrap_admin_state (id, config_fingerprint, user_id, synced_at)
     VALUES (?, ?, ?, CURRENT_TIMESTAMP)
     ON CONFLICT(id) DO UPDATE SET
       config_fingerprint = excluded.config_fingerprint,
       user_id = excluded.user_id,
       synced_at = CURRENT_TIMESTAMP`,
  )
    .bind(BOOTSTRAP_ADMIN_STATE_ROW_ID, String(configFingerprint), String(userId))
    .run();
}

function bytesToHex(bytes) {
  let out = '';
  for (const value of bytes) {
    out += value.toString(16).padStart(2, '0');
  }
  return out;
}

function normalizeUserRole(value) {
  const role = String(value || '').trim().toLowerCase();
  if (role === USER_ROLE_ADMIN || role === USER_ROLE_READONLY) {
    return role;
  }
  return USER_ROLE_USER;
}

function normalizeManagedUserRolePayload(payload) {
  if (payload.role !== undefined) {
    return requireUserRole(payload.role);
  }
  if (payload.isAdmin === true) {
    return USER_ROLE_ADMIN;
  }
  if (payload.canUpload === false) {
    return USER_ROLE_READONLY;
  }
  return USER_ROLE_USER;
}

function requireUserRole(value) {
  const role = String(value || '').trim().toLowerCase();
  if (role === USER_ROLE_ADMIN || role === USER_ROLE_USER || role === USER_ROLE_READONLY) {
    return role;
  }
  throw new HttpError(400, 'role must be admin, user, or readonly');
}

async function sendAuthEmail({ env, debugLabel, debugUrl, toEmail, subject, text, html }) {
  const resendApiKey = String(env.RESEND_API_KEY || '').trim();
  const fromEmail = String(env.AUTH_EMAIL_FROM || '').trim();
  if (!resendApiKey || !fromEmail) {
    if (toBoolean(env.EXPOSE_DEBUG_AUTH_LINKS)) {
      console.info(`[auth debug] ${debugLabel}: ${debugUrl}`);
    } else {
      console.warn(`[auth] email delivery skipped (RESEND_API_KEY / AUTH_EMAIL_FROM not configured): ${debugLabel}`);
    }
    return;
  }

  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${resendApiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: fromEmail,
      to: [toEmail],
      subject,
      html,
      text,
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new HttpError(502, `failed to send auth email: ${response.status} ${body}`);
  }
}

async function serveFrontend(request, env, url) {
  if (url.pathname === '/') {
    return Response.redirect(new URL(`${CONSOLE_BASE_PATH}/`, request.url), 302);
  }

  const assets = getAssetsBinding(env);
  if (assets === null) {
    return frontendFallbackResponse();
  }

  let assetPath = '/';
  if (url.pathname.startsWith(`${CONSOLE_BASE_PATH}/assets/`)) {
    assetPath = url.pathname.slice(CONSOLE_BASE_PATH.length);
  } else if (url.pathname === `${CONSOLE_BASE_PATH}/favicon.ico`) {
    assetPath = '/favicon.ico';
  } else if (!isConsoleAppPath(url.pathname)) {
    return jsonResponse(404, { message: 'not found' });
  }

  const assetRequest = new Request(new URL(assetPath, 'https://assets.invalid'), request);
  return assets.fetch(assetRequest);
}

function isConsoleAppPath(pathname) {
  return pathname === CONSOLE_BASE_PATH || pathname === `${CONSOLE_BASE_PATH}/` || pathname.startsWith(`${CONSOLE_BASE_PATH}/`);
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

function buildConsoleFallbackHtml() {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Feel8 Asset Cloud</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #0c1220; color: #edf2ff; }
      main { max-width: 760px; margin: 48px auto; padding: 24px; border: 1px solid #30456b; border-radius: 12px; background: #121c31; }
      h1 { margin-top: 0; }
      code { background: #0b1426; padding: 2px 6px; border-radius: 6px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Feel8 Asset Cloud</h1>
      <p>Frontend assets are not available yet.</p>
      <p>Build the Vite app first:</p>
      <p><code>npm run web:build</code></p>
      <p>Then open <code>${CONSOLE_BASE_PATH}/</code> in the browser.</p>
    </main>
  </body>
</html>`;
}

function secondsFromEnv(value, fallback) {
  const parsed = Number.parseInt(String(value || fallback), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function requireBodyString(value, message) {
  const text = String(value || '').trim();
  if (!text) {
    throw new HttpError(400, message);
  }
  return text;
}

function requireQueryString(value, message) {
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

function normalizeUsername(value) {
  return requireBodyString(value, 'username is required').toLowerCase();
}

class HttpError extends Error {
  constructor(status, message, payload = {}) {
    super(String(message || 'request failed'));
    this.status = status;
    this.payload = payload;
  }
}
