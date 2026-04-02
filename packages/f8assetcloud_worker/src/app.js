import { betterAuth, generateId } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { hashPassword } from 'better-auth/crypto';
import { admin, username } from 'better-auth/plugins';
import { drizzle } from 'drizzle-orm/d1';
import { Hono } from 'hono';

import { authSchema } from './auth_schema.js';
import { AssetConflictError, AssetNotFoundError, AssetPermissionError, AssetRepository } from './repository.js';

const AUTH_BASE_PATH = '/api/auth';
const CONSOLE_BASE_PATH = '/console';
const MANAGEMENT_API_BASE_PATH = '/v1/management';
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
const bootstrapAdminInitByDb = new WeakMap();

export function createApp() {
  const app = new Hono();

  app.all(`${AUTH_BASE_PATH}/*`, async (c) => {
    validateEnv(c.env);
    const auth = createAuth(c.env, c.req.raw);
    await ensureBootstrapAdmin({ env: c.env });
    return auth.handler(c.req.raw);
  });

  app.use('/v1/*', async (c, next) => {
    validateEnv(c.env);
    const auth = createAuth(c.env, c.req.raw);
    await ensureBootstrapAdmin({ env: c.env });
    c.set('auth', auth);
    c.set('repo', new AssetRepository(c.env.DB));
    await next();
  });

  app.get('/v1/auth/providers', (c) => c.json({
    google: hasGoogleProvider(c.env),
  }));

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

  app.get('/v1/me', async (c) => {
    const user = await requireAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
    return c.json(toApiUser(user));
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

  app.get('/v1/search', async (c) => {
    const repo = c.get('repo');
    const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
    const result = await repo.searchAssets({
      assetType: c.req.query('assetType') || '',
      userId: viewer === null ? null : viewer.userId,
      query: c.req.query('q') || '',
      visibility: c.req.query('visibility') || '',
      owner: c.req.query('owner') || '',
      cursor: c.req.query('cursor') || '',
    });
    return c.json(result);
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
    const user = await requireAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
    const payload = await readJsonBody(c.req.raw);
    return c.json(await repo.createVariant({ payload, user }));
  });

  app.get('/v1/components', async (c) => {
    const repo = c.get('repo');
    const viewer = await optionalAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
    const result = await repo.listComponents({
      userId: viewer === null ? null : viewer.userId,
      query: c.req.query('q') || '',
      visibility: c.req.query('visibility') || '',
      owner: c.req.query('owner') || '',
      cursor: c.req.query('cursor') || '',
    });
    return c.json(result);
  });

  app.post('/v1/components', async (c) => {
    const repo = c.get('repo');
    const user = await requireAuthenticatedUser({ auth: c.get('auth'), request: c.req.raw });
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
      const user = await requireAuthenticatedUser({ auth, request });
      const payload = await readJsonBody(request);
      const result = assetType === 'variant'
        ? await repo.updateVariant({ variantId: assetId, payload, user })
        : await repo.updateComponent({ componentId: assetId, payload, user });
      return jsonResponse(200, result);
    }
    if (request.method === 'DELETE') {
      const user = await requireAuthenticatedUser({ auth, request });
      if (assetType === 'variant') {
        await repo.deleteVariant({ variantId: assetId, userId: user.userId });
      } else {
        await repo.deleteComponent({ componentId: assetId, userId: user.userId });
      }
      return jsonResponse(200, {});
    }
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
    const user = await requireAuthenticatedUser({ auth, request });
    const payload = await readJsonBody(request);
    const result = assetType === 'variant'
      ? await repo.forkVariant({ variantId: assetId, payload, user })
      : await repo.forkComponent({ componentId: assetId, payload, user });
    return jsonResponse(200, result);
  }

  return jsonResponse(404, { message: 'not found' });
}

async function routeManagementRequest({ managementUser, auth, repo, request, url }) {
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
    const created = await auth.api.createUser({
      body: {
        email: requireBodyString(payload.email, 'email is required'),
        password: requireBodyString(payload.password, 'password is required'),
        name: displayName,
        role: Boolean(payload.isAdmin) ? 'admin' : 'user',
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
    if (parts.suffix.length === 1 && parts.suffix[0] === 'assets') {
      const result = await repo.listAssetsByOwnerForManagement({
        ownerUserId: userId,
        assetType: url.searchParams.get('assetType') || '',
        includeDeleted: url.searchParams.get('includeDeleted') || '',
        cursor: url.searchParams.get('cursor') || '',
      });
      return jsonResponse(200, result);
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
    if (payload.isAdmin !== undefined) {
      await auth.api.setRole({
        body: {
          userId: parts.userId,
          role: Boolean(payload.isAdmin) ? 'admin' : 'user',
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

  if (request.method === 'GET' && url.pathname === `${MANAGEMENT_API_BASE_PATH}/assets`) {
    const result = await repo.listManagedAssets({
      assetType: url.searchParams.get('assetType') || '',
      ownerUserId: url.searchParams.get('ownerUserId') || '',
      query: url.searchParams.get('q') || '',
      includeDeleted: url.searchParams.get('includeDeleted') || '',
      cursor: url.searchParams.get('cursor') || '',
    });
    return jsonResponse(200, result);
  }

  if (request.method === 'GET' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/assets/`)) {
    const assetId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/assets/`);
    if (!assetId) {
      return jsonResponse(404, { message: 'not found' });
    }
    const asset = await repo.getManagedAsset({
      assetId,
      includeDeleted: url.searchParams.get('includeDeleted') || '',
    });
    if (asset === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, asset);
  }

  if (request.method === 'PUT' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/assets/`)) {
    const assetId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/assets/`);
    if (!assetId) {
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
    const current = await repo.getManagedAsset({ assetId, includeDeleted: true });
    if (current === null) {
      return jsonResponse(404, { message: 'asset not found' });
    }
    return jsonResponse(200, current);
  }

  if (request.method === 'DELETE' && url.pathname.startsWith(`${MANAGEMENT_API_BASE_PATH}/assets/`)) {
    const assetId = decodeSinglePathValue(url.pathname, `${MANAGEMENT_API_BASE_PATH}/assets/`);
    if (!assetId) {
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

function createAuth(env, request) {
  const requestUrl = new URL(request.url);
  const baseURL = resolveAuthBaseUrl(env, requestUrl);
  const db = drizzle(env.DB);
  const bootstrapUsername = String(env.BOOTSTRAP_ADMIN_USERNAME || '').trim().toLowerCase();
  const bootstrapDisplayName = String(env.BOOTSTRAP_ADMIN_DISPLAY_NAME || '').trim();
  const socialProviders = {};
  if (hasGoogleProvider(env)) {
    socialProviders.google = {
      clientId: String(env.GOOGLE_CLIENT_ID || '').trim(),
      clientSecret: String(env.GOOGLE_CLIENT_SECRET || '').trim(),
    };
  }

  return betterAuth({
    secret: getAuthSecret(env),
    baseURL,
    basePath: AUTH_BASE_PATH,
    trustedOrigins: [requestUrl.origin, new URL(baseURL).origin],
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
      resetPasswordTokenExpiresIn: secondsFromEnv(env.PASSWORD_RESET_TOKEN_TTL_SECONDS, 1800),
      sendResetPassword: async ({ user, token, url }) => {
        const appUser = toAppUser(user);
        const resetUrl = resolveAuthActionUrl({
          preferredUrl: url,
          fallbackUrl: buildResetPasswordUrl({ env, request, token }),
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
          fallbackUrl: buildVerifyEmailUrl({ env, request, token }),
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
        defaultRole: 'user',
        adminRoles: ['admin'],
      }),
    ],
  });
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
  const existing = await env.DB.prepare(
    'SELECT id FROM user WHERE email = ? OR username = ? LIMIT 1',
  )
    .bind(config.email, config.usernameValue)
    .first();
  const passwordHash = await hashPassword(config.password);
  const timestamp = Date.now();

  if (existing === null) {
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

  const credentialAccount = await env.DB.prepare(
    `SELECT id
     FROM account
     WHERE userId = ? AND providerId = 'credential'
     LIMIT 1`,
  )
    .bind(userId)
    .first();

  if (credentialAccount === null) {
    await env.DB.prepare(
      `INSERT INTO account
         ("id", "accountId", "providerId", "userId", "accessToken", "refreshToken", "idToken", "accessTokenExpiresAt", "refreshTokenExpiresAt", "scope", "password", "createdAt", "updatedAt")
       VALUES (?, ?, 'credential', ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)`,
    )
      .bind(generateId(), userId, userId, passwordHash, timestamp, timestamp)
      .run();
    return;
  }

  await env.DB.prepare(
    `UPDATE account
     SET accountId = ?,
         password = ?,
         updatedAt = ?
     WHERE id = ?`,
  )
    .bind(userId, passwordHash, timestamp, String(credentialAccount.id))
    .run();
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

async function assertUserHasNoAssets(repo, userId) {
  const countRow = await repo._db.prepare(
    'SELECT COUNT(*) AS count FROM asset_heads WHERE owner_user_id = ?',
  )
    .bind(String(userId))
    .first();
  if (Number(countRow?.count || 0) > 0) {
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
    isAdmin: String(user.role || '') === 'admin',
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

function resolveAuthBaseUrl(env, requestUrl) {
  const configured = String(env.AUTH_BASE_URL || '').trim();
  if (configured) {
    return configured;
  }
  return requestUrl.origin;
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
  const apiError = toAuthApiErrorPayload(error);
  if (apiError !== null) {
    return jsonResponse(apiError.status, apiError.payload);
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
  if (error instanceof Error && error.message) {
    if (isUniqueConstraintError(error)) {
      return jsonResponse(409, { message: 'duplicate resource' });
    }
    if (error.message.includes('already exists') || error.message.includes('duplicate')) {
      return jsonResponse(409, { message: error.message });
    }
    if (
      error.message.includes('required') ||
      error.message.includes('must be') ||
      error.message.includes('not found') ||
      error.message.includes('reserved')
    ) {
      return jsonResponse(400, { message: error.message });
    }
  }
  console.error('Unhandled unified asset worker error', error);
  return jsonResponse(500, { message: `internal error: ${error?.name || 'Error'}: ${error?.message || error}` });
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

function buildVerifyEmailUrl({ env, request, token }) {
  const configuredBaseUrl = String(env.AUTH_VERIFY_EMAIL_BASE_URL || '').trim();
  const base = configuredBaseUrl
    ? new URL(configuredBaseUrl)
    : new URL(`${CONSOLE_BASE_PATH}/verify-email`, request.url);
  base.searchParams.set('token', token);
  return base.toString();
}

function buildResetPasswordUrl({ env, request, token }) {
  const configuredBaseUrl = String(env.AUTH_RESET_PASSWORD_BASE_URL || '').trim();
  const base = configuredBaseUrl
    ? new URL(configuredBaseUrl)
    : new URL(`${CONSOLE_BASE_PATH}/reset-password`, request.url);
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

async function sendAuthEmail({ env, debugLabel, debugUrl, toEmail, subject, text, html }) {
  const resendApiKey = String(env.RESEND_API_KEY || '').trim();
  const fromEmail = String(env.AUTH_EMAIL_FROM || '').trim();
  if (!resendApiKey || !fromEmail) {
    if (toBoolean(env.EXPOSE_DEBUG_AUTH_LINKS)) {
      console.info(`[auth debug] ${debugLabel}: ${debugUrl}`);
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

function stringOrDefault(value, fallback) {
  const text = String(value || '').trim();
  return text || fallback;
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
