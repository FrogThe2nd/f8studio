import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { gunzipSync, gzipSync } from 'node:zlib';
import { hashPassword as hashLegacyBetterAuthPassword } from 'better-auth/crypto';

import { createApp, resetWorkerCachesForTesting } from '../src/app.js';
import worker from '../src/index.js';
import { authPasswordHashVersion, hashAuthPassword, verifyAuthPassword } from '../src/password.js';
import { createSqliteD1Database } from '../test_support/sqlite_d1_adapter.js';

const TEST_AUTH_SECRET = '0123456789abcdef0123456789abcdef';
const TEST_PASSWORD = 'password123';
const TEST_PASSWORD_2 = 'password456';
const TEST_PASSWORD_3 = 'password789';
const CONSOLE_BASE_PATH = '/console';
const MANAGEMENT_API_BASE_PATH = '/v1/management';

const migrationsDir = path.join(import.meta.dirname, '..', 'migrations');
const migrationsSql = fs.readdirSync(migrationsDir)
  .filter((filename) => filename.endsWith('.sql'))
  .sort()
  .map((filename) => fs.readFileSync(path.join(migrationsDir, filename), 'utf8'))
  .join('\n\n');

function createEnv({ allowUserRegistration = true, ...overrides } = {}) {
  const env = {
    DB: createSqliteD1Database({ migrationsSql }),
    BETTER_AUTH_SECRET: TEST_AUTH_SECRET,
    BOOTSTRAP_ADMIN_USERNAME: 'admin',
    BOOTSTRAP_ADMIN_DISPLAY_NAME: 'Administrator',
    BOOTSTRAP_ADMIN_PASSWORD: TEST_PASSWORD,
    BOOTSTRAP_ADMIN_EMAIL: 'admin@example.com',
    EXPOSE_DEBUG_AUTH_LINKS: 'true',
    ...overrides,
  };
  env.DB.prepare(
    'UPDATE site_settings SET allow_user_registration = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1',
  )
    .bind(allowUserRegistration ? 1 : 0)
    .run();
  return env;
}

async function jsonRequest(app, env, pathname, { method, payload, cookie, origin } = {}) {
  const headers = {};
  if (payload !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (cookie) {
    headers.cookie = cookie;
  }
  if (origin) {
    headers.origin = origin;
  }
  const request = new Request(`http://worker.test${pathname}`, {
    method: method || 'GET',
    headers,
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const response = await app.fetch(request, env, {});
  const bodyText = await response.text();
  return {
    status: response.status,
    json: bodyText ? JSON.parse(bodyText) : {},
    text: bodyText,
    headers: response.headers,
  };
}

async function captureConsoleInfo(run) {
  const logs = [];
  const originalInfo = console.info;
  console.info = (...args) => {
    logs.push(args.map((value) => String(value)).join(' '));
  };
  try {
    const result = await run();
    return { result, logs };
  } finally {
    console.info = originalInfo;
  }
}

function extractDebugToken(logs, label) {
  const prefix = `[auth debug] ${label}: `;
  const line = [...logs].reverse().find((entry) => entry.startsWith(prefix));
  if (!line) {
    return '';
  }
  const url = new URL(line.slice(prefix.length));
  const queryToken = String(url.searchParams.get('token') || '');
  if (queryToken) {
    return queryToken;
  }
  const pathParts = url.pathname.split('/').filter((part) => part.length > 0);
  const resetPasswordIndex = pathParts.findIndex((part) => part === 'reset-password');
  if (resetPasswordIndex >= 0 && resetPasswordIndex + 1 < pathParts.length) {
    return String(pathParts[resetPasswordIndex + 1] || '');
  }
  return '';
}

function responseCookie(headers) {
  const setCookie = headers.get('set-cookie');
  return setCookie ? String(setCookie).split(';')[0] : '';
}

function wrapBlobRowsAsDataArrays(db) {
  const originalPrepare = db.prepare.bind(db);

  function wrapRow(row) {
    if (row === null || row === undefined) {
      return row;
    }
    if (!Object.hasOwn(row, 'content')) {
      return row;
    }
    const content = row.content;
    if (content instanceof Uint8Array) {
      return {
        ...row,
        content: {
          data: Array.from(content),
        },
      };
    }
    if (ArrayBuffer.isView(content)) {
      return {
        ...row,
        content: {
          data: Array.from(new Uint8Array(content.buffer, content.byteOffset, content.byteLength)),
        },
      };
    }
    return row;
  }

  function wrapPrepared(prepared) {
    return {
      bind(...values) {
        return wrapPrepared(prepared.bind(...values));
      },
      async first() {
        return wrapRow(await prepared.first());
      },
      async all() {
        const result = await prepared.all();
        return {
          ...result,
          results: Array.isArray(result.results) ? result.results.map((row) => wrapRow(row)) : result.results,
        };
      },
      async run() {
        return prepared.run();
      },
      async raw() {
        return prepared.raw();
      },
    };
  }

  db.prepare = function prepare(sql) {
    return wrapPrepared(originalPrepare(sql));
  };
}

async function signUpUser(app, env, { username, email, displayName, password = TEST_PASSWORD }) {
  const { result, logs } = await captureConsoleInfo(() => jsonRequest(app, env, '/api/auth/sign-up/email', {
    method: 'POST',
    payload: {
      email,
      password,
      name: displayName,
      username,
      displayUsername: displayName,
    },
  }));
  return {
    ...result,
    verifyToken: extractDebugToken(logs, 'verify email'),
  };
}

async function verifyUserEmail(app, env, token) {
  return jsonRequest(app, env, `/v1/auth/verify-email?token=${encodeURIComponent(token)}`);
}

async function signInUser(app, env, { username, password = TEST_PASSWORD }) {
  const result = await jsonRequest(app, env, '/api/auth/sign-in/username', {
    method: 'POST',
    payload: {
      username,
      password,
    },
  });
  return {
    ...result,
    cookie: responseCookie(result.headers),
  };
}

async function requestPasswordReset(app, env, email) {
  const { result, logs } = await captureConsoleInfo(() => jsonRequest(app, env, '/api/auth/request-password-reset', {
    method: 'POST',
    payload: { email },
  }));
  return {
    ...result,
    resetToken: extractDebugToken(logs, 'reset password'),
  };
}

async function createVerifiedSession(app, env, { username, email, displayName, password = TEST_PASSWORD }) {
  const signedUp = await signUpUser(app, env, { username, email, displayName, password });
  assert.equal(signedUp.status, 200);
  assert.ok(signedUp.verifyToken);

  const verified = await verifyUserEmail(app, env, signedUp.verifyToken);
  assert.equal(verified.status, 200);

  const signedIn = await signInUser(app, env, { username, password });
  assert.equal(signedIn.status, 200);
  assert.ok(signedIn.cookie);

  return {
    userId: String(signedUp.json.user.id),
    cookie: signedIn.cookie,
    signUp: signedUp,
    signIn: signedIn,
  };
}

function variantPayload({ variantId, name, visibility = 'private', revision } = {}) {
  return {
    record: {
      variantId: variantId || 'variant-1',
      kind: 'operator',
      baseNodeType: 'svc.base.op',
      serviceClass: 'svc.test',
      operatorClass: 'op.test',
      name: name || 'Variant 1',
      description: 'desc',
      tags: ['vision'],
      spec: { label: name || 'Variant 1' },
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
    },
    visibility,
    revision,
    changeSummary: 'save',
  };
}

function componentPayload({ componentId, name, visibility = 'private', revision } = {}) {
  return {
    record: {
      componentId: componentId || 'component-1',
      name: name || 'Component 1',
      description: 'published session',
      tags: ['session'],
      schemaVersion: 'f8studio-session/1',
      content: {
        schemaVersion: 'f8studio-session/1',
        layout: {
          nodes: {
            n1: {
              type_: 'svc.f8.implayer',
              custom: {
                authCookiesFile: '',
              },
            },
          },
          connections: [],
        },
      },
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
    },
    visibility,
    revision,
    changeSummary: 'save',
  };
}

test('auth flows use Better Auth cookie sessions and email actions', async (t) => {
  const env = createEnv();
  t.after(() => {
    resetWorkerCachesForTesting();
    env.DB.close();
  });
  const app = createApp();

  const providers = await jsonRequest(app, env, '/v1/auth/providers');
  assert.equal(providers.status, 200);
  assert.equal(providers.json.google, false);

  const siteSettings = await jsonRequest(app, env, '/v1/site-settings');
  assert.equal(siteSettings.status, 200);
  assert.equal(siteSettings.json.allowUserRegistration, true);

  const signedUp = await signUpUser(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });
  assert.equal(signedUp.status, 200);
  assert.equal(signedUp.json.user.username, 'alice');
  assert.equal(signedUp.json.user.emailVerified, false);
  assert.ok(signedUp.verifyToken);

  const loginBeforeVerify = await signInUser(app, env, {
    username: 'alice',
  });
  assert.equal(loginBeforeVerify.status, 403);
  assert.equal(loginBeforeVerify.json.code, 'EMAIL_NOT_VERIFIED');

  const verified = await verifyUserEmail(app, env, signedUp.verifyToken);
  assert.equal(verified.status, 200);
  assert.equal(verified.json.verified, true);

  const duplicate = await jsonRequest(app, env, '/api/auth/sign-up/email', {
    method: 'POST',
    payload: {
      email: 'alice2@example.com',
      password: TEST_PASSWORD,
      name: 'Alice 2',
      username: 'alice',
      displayUsername: 'Alice 2',
    },
  });
  assert.equal(duplicate.status, 400);
  assert.equal(duplicate.json.code, 'USERNAME_IS_ALREADY_TAKEN');

  const loginFail = await signInUser(app, env, {
    username: 'alice',
    password: 'wrong-password',
  });
  assert.equal(loginFail.status, 401);

  const signedIn = await signInUser(app, env, {
    username: 'alice',
  });
  assert.equal(signedIn.status, 200);
  assert.ok(signedIn.cookie);

  const me = await jsonRequest(app, env, '/v1/me', { cookie: signedIn.cookie });
  assert.equal(me.status, 200);
  assert.equal(me.json.displayName, 'Alice');
  assert.equal(me.json.email, 'alice@example.com');
  assert.equal(me.json.emailVerified, true);

  const changedPassword = await jsonRequest(app, env, '/v1/me/password', {
    method: 'POST',
    cookie: signedIn.cookie,
    payload: {
      currentPassword: TEST_PASSWORD,
      newPassword: TEST_PASSWORD_2,
    },
  });
  assert.equal(changedPassword.status, 200);

  const oldLogin = await signInUser(app, env, {
    username: 'alice',
    password: TEST_PASSWORD,
  });
  assert.equal(oldLogin.status, 401);

  const newLogin = await signInUser(app, env, {
    username: 'alice',
    password: TEST_PASSWORD_2,
  });
  assert.equal(newLogin.status, 200);

  const resetRequest = await requestPasswordReset(app, env, 'alice@example.com');
  assert.equal(resetRequest.status, 200);
  assert.equal(resetRequest.json.status, true);
  assert.ok(resetRequest.resetToken);

  const secondResetRequest = await requestPasswordReset(app, env, 'alice@example.com');
  assert.equal(secondResetRequest.status, 200);
  assert.equal(secondResetRequest.json.status, true);
  assert.ok(secondResetRequest.resetToken);

  const reset = await jsonRequest(app, env, '/v1/auth/reset-password', {
    method: 'POST',
    payload: {
      token: resetRequest.resetToken,
      newPassword: TEST_PASSWORD_3,
    },
  });
  assert.equal(reset.status, 200);
  assert.equal(reset.json.reset, true);

  const resetLogin = await signInUser(app, env, {
    username: 'alice',
    password: TEST_PASSWORD_3,
  });
  assert.equal(resetLogin.status, 200);

  const reservedUsername = await jsonRequest(app, env, '/api/auth/sign-up/email', {
    method: 'POST',
    payload: {
      email: 'owner@example.com',
      password: TEST_PASSWORD,
      name: 'Owner User',
      username: 'owner',
      displayUsername: 'Owner User',
    },
  });
  assert.equal(reservedUsername.status, 400);
  assert.equal(reservedUsername.json.code, 'INVALID_USERNAME');

  const reservedDisplayName = await jsonRequest(app, env, '/api/auth/sign-up/email', {
    method: 'POST',
    payload: {
      email: 'support@example.com',
      password: TEST_PASSWORD,
      name: 'Support',
      username: 'normal_user',
      displayUsername: 'Support',
    },
  });
  assert.equal(reservedDisplayName.status, 400);
  assert.equal(reservedDisplayName.json.code, 'INVALID_DISPLAY_USERNAME');
});

test('bootstrap admin sync avoids rotating credentials after a cold-cache login', async (t) => {
  const env = createEnv({ allowUserRegistration: false });
  t.after(() => {
    resetWorkerCachesForTesting();
    env.DB.close();
  });

  const firstApp = createApp();
  const firstLogin = await signInUser(firstApp, env, {
    username: 'admin',
  });
  assert.equal(firstLogin.status, 200);
  assert.ok(firstLogin.cookie);

  const firstAccount = await env.DB.prepare(
    `SELECT
       a.id,
       a.password,
       a.updatedAt AS updated_at
     FROM account a
     JOIN user u ON u.id = a.userId
     WHERE u.username = ? AND a.providerId = 'credential'
     LIMIT 1`,
  )
    .bind('admin')
    .first();
  assert.notEqual(firstAccount, null);

  const bootstrapState = await env.DB.prepare(
    `SELECT config_fingerprint, user_id
     FROM bootstrap_admin_state
     WHERE id = 1`,
  ).first();
  assert.notEqual(bootstrapState, null);

  resetWorkerCachesForTesting();

  const secondApp = createApp();
  const secondLogin = await signInUser(secondApp, env, {
    username: 'admin',
  });
  assert.equal(secondLogin.status, 200);
  assert.ok(secondLogin.cookie);

  const secondAccount = await env.DB.prepare(
    `SELECT
       a.id,
       a.password,
       a.updatedAt AS updated_at
     FROM account a
     JOIN user u ON u.id = a.userId
     WHERE u.username = ? AND a.providerId = 'credential'
     LIMIT 1`,
  )
    .bind('admin')
    .first();
  assert.notEqual(secondAccount, null);
  assert.equal(secondAccount.id, firstAccount.id);
  assert.equal(secondAccount.password, firstAccount.password);
  assert.equal(secondAccount.updated_at, firstAccount.updated_at);
});

test('worker password hash verifies credentials without Better Auth scrypt', async () => {
  const hash = await hashAuthPassword(TEST_PASSWORD);
  assert.match(hash, /^f8pbkdf2-sha256-v1\$50000\$/);
  assert.equal(hash.includes(':'), false);
  assert.equal(await verifyAuthPassword({ hash, password: TEST_PASSWORD }), true);
  assert.equal(await verifyAuthPassword({ hash, password: TEST_PASSWORD_2 }), false);
});

test('bootstrap admin sync replaces legacy Better Auth password hashes', async (t) => {
  const env = createEnv({ allowUserRegistration: false });
  t.after(() => {
    resetWorkerCachesForTesting();
    env.DB.close();
  });

  const app = createApp();
  const firstLogin = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(firstLogin.status, 200);

  const legacyHash = await hashLegacyBetterAuthPassword(TEST_PASSWORD_2);
  await env.DB.prepare(
    `UPDATE account
     SET password = ?
     WHERE providerId = 'credential'
       AND userId = (
         SELECT id
         FROM user
         WHERE username = ?
         LIMIT 1
       )`,
  )
    .bind(legacyHash, 'admin')
    .run();
  await env.DB.prepare('DELETE FROM bootstrap_admin_state WHERE id = 1').run();

  resetWorkerCachesForTesting();

  const syncedApp = createApp();
  const syncedLogin = await signInUser(syncedApp, env, {
    username: 'admin',
  });
  assert.equal(syncedLogin.status, 200);
  assert.ok(syncedLogin.cookie);

  const account = await env.DB.prepare(
    `SELECT a.password
     FROM account a
     JOIN user u ON u.id = a.userId
     WHERE u.username = ? AND a.providerId = 'credential'
     LIMIT 1`,
  )
    .bind('admin')
    .first();
  assert.notEqual(account, null);
  assert.match(String(account.password), /^f8pbkdf2-sha256-v1\$50000\$/);
  assert.equal(await verifyAuthPassword({ hash: account.password, password: TEST_PASSWORD }), true);

  const bootstrapState = await env.DB.prepare(
    `SELECT config_fingerprint
     FROM bootstrap_admin_state
     WHERE id = 1`,
  ).first();
  assert.notEqual(bootstrapState, null);
  assert.ok(String(bootstrapState.config_fingerprint || '').length > 0);
  assert.ok(authPasswordHashVersion().startsWith('f8pbkdf2-sha256-v1:'));
});

test('sign-out requires Origin header and deletes the current session when provided', async (t) => {
  const env = createEnv({ allowUserRegistration: false });
  t.after(() => {
    resetWorkerCachesForTesting();
    env.DB.close();
  });
  const app = createApp();

  const signedIn = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(signedIn.status, 200);
  assert.ok(signedIn.cookie);

  const beforeSignOut = await env.DB.prepare(
    'SELECT COUNT(*) AS count FROM session',
  ).first();
  assert.equal(Number(beforeSignOut?.count ?? 0), 1);

  const missingOrigin = await jsonRequest(app, env, '/api/auth/sign-out', {
    method: 'POST',
    payload: {},
    cookie: signedIn.cookie,
  });
  assert.equal(missingOrigin.status, 403);
  assert.equal(missingOrigin.json.code, 'MISSING_OR_NULL_ORIGIN');

  const afterRejectedSignOut = await env.DB.prepare(
    'SELECT COUNT(*) AS count FROM session',
  ).first();
  assert.equal(Number(afterRejectedSignOut?.count ?? 0), 1);

  const acceptedSignOut = await jsonRequest(app, env, '/api/auth/sign-out', {
    method: 'POST',
    payload: {},
    cookie: signedIn.cookie,
    origin: 'http://worker.test',
  });
  assert.equal(acceptedSignOut.status, 200);
  assert.equal(acceptedSignOut.json.success, true);

  const afterAcceptedSignOut = await env.DB.prepare(
    'SELECT COUNT(*) AS count FROM session',
  ).first();
  assert.equal(Number(afterAcceptedSignOut?.count ?? 0), 0);
});

test('providers endpoint reflects Google auth configuration', async (t) => {
  const env = createEnv({
    GOOGLE_CLIENT_ID: 'google-client-id',
    GOOGLE_CLIENT_SECRET: 'google-client-secret',
  });
  t.after(() => env.DB.close());
  const app = createApp();

  const providers = await jsonRequest(app, env, '/v1/auth/providers');
  assert.equal(providers.status, 200);
  assert.equal(providers.json.google, true);
});

test('openapi endpoints expose the audited worker contract', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const openapi = await jsonRequest(app, env, '/openapi.json');
  assert.equal(openapi.status, 200);
  assert.equal(openapi.json.info.title, 'Feel8 Asset Cloud API');
  assert.ok(openapi.json.paths['/v1/auth/providers']);
  assert.ok(openapi.json.paths['/v1/site-settings']);
  assert.ok(openapi.json.paths['/v1/me']);
  assert.equal(openapi.json.paths['/v1/search'], undefined);
  assert.ok(openapi.json.paths['/v1/components']);
  assert.ok(openapi.json.paths['/v1/components/{componentId}']);
  assert.ok(openapi.json.paths['/v1/components/{componentId}/content']);
  assert.ok(openapi.json.paths['/v1/components/{componentId}/meta']);
  assert.ok(openapi.json.paths['/v1/components/{componentId}/versions']);
  assert.ok(openapi.json.paths['/v1/components/{componentId}/subscribe']);
  assert.ok(openapi.json.paths['/v1/variants']);
  assert.ok(openapi.json.paths['/v1/variants/{variantId}']);
  assert.ok(openapi.json.paths['/v1/variants/{variantId}/content']);
  assert.ok(openapi.json.paths['/v1/variants/{variantId}/meta']);
  assert.ok(openapi.json.paths['/v1/variants/{variantId}/versions']);
  assert.ok(openapi.json.paths['/v1/variants/{variantId}/subscribe']);
  assert.ok(openapi.json.paths['/v1/management/users']);
  assert.ok(openapi.json.paths['/v1/management/users/{userId}']);
  assert.ok(openapi.json.paths['/v1/management/site-settings']);
  assert.ok(openapi.json.paths['/v1/management/assets/purge-all']);
  assert.ok(openapi.json.paths['/v1/management/components']);
  assert.ok(openapi.json.paths['/v1/management/components/{componentId}']);
  assert.ok(openapi.json.paths['/v1/management/variants']);
  assert.ok(openapi.json.paths['/v1/management/variants/{variantId}']);
  assert.equal(openapi.json.paths['/v1/management/users/{userId}/assets'], undefined);
  assert.equal(openapi.json.paths['/v1/management/assets'], undefined);
  assert.equal(openapi.json.paths['/v1/management/assets/{assetId}'], undefined);

  const docsRequest = new Request('http://worker.test/docs');
  const docsResponse = await app.fetch(docsRequest, env, {});
  const docsHtml = await docsResponse.text();
  assert.equal(docsResponse.status, 200);
  assert.match(docsHtml, /openapi\.json/);
});

test('hot asset list queries use composite indexes without temp sorting', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());

  const componentPlan = await env.DB.prepare(
    `EXPLAIN QUERY PLAN
     SELECT
       h.*,
       COALESCE(u.displayUsername, u.name) AS owner_display_name,
       s.subscribed_at,
       s.last_seen_revision,
       v.created_by_user_id,
       v.change_summary,
       v.version_number,
       v.revision
     FROM asset_heads h
     JOIN asset_versions v
       ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
     LEFT JOIN user u ON u.id = h.owner_user_id
     LEFT JOIN asset_subscriptions s
       ON s.asset_id = h.asset_id AND s.subscriber_user_id = ?
     WHERE h.deleted_at IS NULL AND h.asset_type = ? AND h.visibility = 'public'
     ORDER BY LOWER(h.name), h.asset_id
     LIMIT ? OFFSET ?`,
  )
    .bind('', 'component', 101, 0)
    .all();
  const componentPlanDetails = (componentPlan.results || []).map((row) => String(row.detail || ''));
  assert.ok(componentPlanDetails.some((detail) => detail.includes('idx_asset_heads_type_visibility_name')));
  assert.equal(componentPlanDetails.some((detail) => detail.includes('USE TEMP B-TREE FOR ORDER BY')), false);

  const managementPlan = await env.DB.prepare(
    `EXPLAIN QUERY PLAN
     SELECT
       h.*,
       COALESCE(u.displayUsername, u.name) AS owner_display_name,
       v.created_at AS version_created_at,
       v.created_by_user_id,
       v.change_summary,
       v.version_number,
       v.revision
     FROM asset_heads h
     JOIN asset_versions v
       ON v.asset_id = h.asset_id AND v.version_number = h.latest_version_number
     LEFT JOIN user u ON u.id = h.owner_user_id
     WHERE h.deleted_at IS NULL
     ORDER BY h.updated_at DESC, h.asset_id
     LIMIT ? OFFSET ?`,
  )
    .bind(101, 0)
    .all();
  const managementPlanDetails = (managementPlan.results || []).map((row) => String(row.detail || ''));
  assert.ok(managementPlanDetails.some((detail) => detail.includes('idx_asset_heads_deleted_updated')));
  assert.equal(managementPlanDetails.some((detail) => detail.includes('USE TEMP B-TREE FOR ORDER BY')), false);
});

test('variant asset lifecycle works with Better Auth cookie sessions', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });
  const bob = await createVerifiedSession(app, env, {
    username: 'bob',
    email: 'bob@example.com',
    displayName: 'Bob',
  });

  const created = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'alice-variant', name: 'Alice Private', visibility: 'private' }),
  });
  assert.equal(created.status, 200);
  assert.equal(created.json.variantId, 'alice-variant');
  assert.equal(created.json.assetType, 'variant');
  assert.equal(created.json.revision, 'r1');
  assert.equal(created.json.editable, true);
  const assetHeadColumns = await env.DB.prepare("PRAGMA table_info(asset_heads)").all();
  assert.equal(
    Array.isArray(assetHeadColumns.results) && assetHeadColumns.results.some((column) => String(column.name) === 'content'),
    false,
  );
  const storedVariantVersion = await env.DB.prepare(
    `SELECT content
     FROM asset_versions
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind('alice-variant')
    .first();
  const storedVariantSpec = JSON.parse(gunzipSync(Buffer.from(storedVariantVersion.content)).toString('utf-8'));
  assert.equal(storedVariantSpec.label, 'Alice Private');
  assert.equal(storedVariantSpec.record, undefined);
  assert.equal(storedVariantSpec.name, undefined);
  const variantDetails = await env.DB.prepare(
    'SELECT variant_kind, base_node_type, service_class, operator_class FROM variant_details WHERE asset_id = ?',
  )
    .bind('alice-variant')
    .first();
  assert.equal(String(variantDetails?.variant_kind || ''), 'operator');
  assert.equal(String(variantDetails?.base_node_type || ''), 'svc.base.op');
  assert.equal(String(variantDetails?.service_class || ''), 'svc.test');
  assert.equal(String(variantDetails?.operator_class || ''), 'op.test');

  const publicListBefore = await jsonRequest(app, env, '/v1/variants?owner=public');
  assert.equal(publicListBefore.status, 200);
  assert.equal(publicListBefore.json.entries.length, 0);

  const privateByBob = await jsonRequest(app, env, '/v1/variants/alice-variant', { cookie: bob.cookie });
  assert.equal(privateByBob.status, 403);

  const updated = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    cookie: alice.cookie,
    payload: variantPayload({
      variantId: 'alice-variant',
      name: 'Alice Public',
      visibility: 'public',
      revision: created.json.revision,
    }),
  });
  assert.equal(updated.status, 200);
  assert.equal(updated.json.revision, 'r2');
  assert.equal(updated.json.visibility, 'public');

  const publicListAfter = await jsonRequest(app, env, '/v1/variants?owner=public&q=alice');
  assert.equal(publicListAfter.status, 200);
  assert.equal(publicListAfter.json.entries.length, 1);

  const subscribed = await jsonRequest(app, env, '/v1/variants/alice-variant/subscribe', {
    method: 'POST',
    cookie: bob.cookie,
  });
  assert.equal(subscribed.status, 200);
  assert.equal(subscribed.json.subscribed, true);
  assert.equal(subscribed.json.editable, false);

  const subscribedList = await jsonRequest(app, env, '/v1/variants?owner=subscribed', {
    cookie: bob.cookie,
  });
  assert.equal(subscribedList.status, 200);
  assert.equal(subscribedList.json.entries.length, 1);
  assert.equal(subscribedList.json.entries[0].variantId, 'alice-variant');

  const forbiddenEdit = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    cookie: bob.cookie,
    payload: variantPayload({
      variantId: 'alice-variant',
      name: 'Bob Edit',
      visibility: 'public',
      revision: updated.json.revision,
    }),
  });
  assert.equal(forbiddenEdit.status, 403);

  const history = await jsonRequest(app, env, '/v1/variants/alice-variant/versions', { cookie: alice.cookie });
  assert.equal(history.status, 200);
  assert.equal(history.json.versions.length, 2);
  assert.equal(history.json.versions[0].variantId, 'alice-variant');
  assert.equal(history.json.versions[0].versionNumber, 2);

  const oldVersion = await jsonRequest(app, env, '/v1/variants/alice-variant/versions/1', { cookie: alice.cookie });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.variantId, 'alice-variant');
  assert.equal(oldVersion.json.hasContent, true);
  const oldVersionContent = await jsonRequest(app, env, '/v1/variants/alice-variant/versions/1/content', { cookie: alice.cookie });
  assert.equal(oldVersionContent.status, 200);
  assert.equal(oldVersionContent.json.record.name, 'Alice Public');
  assert.equal(oldVersionContent.json.record.spec.label, 'Alice Private');

  const conflict = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    cookie: alice.cookie,
    payload: variantPayload({
      variantId: 'alice-variant',
      name: 'Stale Update',
      visibility: 'public',
      revision: 'r1',
    }),
  });
  assert.equal(conflict.status, 409);
  assert.equal(conflict.json.revision, 'r2');

  const variantHeadBeforeMetaPatch = await env.DB.prepare(
    `SELECT latest_version_number, latest_revision, updated_at, schema_version
     FROM asset_heads
     WHERE asset_id = ?`,
  )
    .bind('alice-variant')
    .first();

  const metadataPatched = await jsonRequest(app, env, '/v1/variants/alice-variant/meta', {
    method: 'PATCH',
    cookie: alice.cookie,
    payload: {
      name: 'Alice Public Metadata',
      description: 'Metadata only update',
      tags: ['meta', 'variant'],
    },
  });
  assert.equal(metadataPatched.status, 200);
  assert.equal(metadataPatched.json.name, 'Alice Public Metadata');
  assert.equal(metadataPatched.json.description, 'Metadata only update');
  assert.deepEqual(metadataPatched.json.tags, ['meta', 'variant']);
  assert.equal(metadataPatched.json.latestVersionNumber, 2);
  assert.equal(metadataPatched.json.latestRevision, 'r2');

  const variantHeadAfterMetaPatch = await env.DB.prepare(
    `SELECT latest_version_number, latest_revision, updated_at, schema_version
     FROM asset_heads
     WHERE asset_id = ?`,
  )
    .bind('alice-variant')
    .first();
  assert.equal(Number(variantHeadAfterMetaPatch?.latest_version_number ?? 0), 2);
  assert.equal(String(variantHeadAfterMetaPatch?.latest_revision || ''), 'r2');
  assert.equal(String(variantHeadAfterMetaPatch?.schema_version || ''), '');
  assert.notEqual(
    String(variantHeadAfterMetaPatch?.updated_at || ''),
    String(variantHeadBeforeMetaPatch?.updated_at || ''),
  );

  const variantVersionsAfterMetaPatch = await jsonRequest(app, env, '/v1/variants/alice-variant/versions', { cookie: alice.cookie });
  assert.equal(variantVersionsAfterMetaPatch.status, 200);
  assert.equal(variantVersionsAfterMetaPatch.json.versions.length, 2);

  const forbiddenVariantMetadataPatch = await jsonRequest(app, env, '/v1/variants/alice-variant/meta', {
    method: 'PATCH',
    cookie: bob.cookie,
    payload: {
      name: 'Bob Variant Edit',
      description: 'forbidden',
      tags: ['forbidden'],
    },
  });
  assert.equal(forbiddenVariantMetadataPatch.status, 403);

  const forked = await jsonRequest(app, env, '/v1/variants/alice-variant/fork', {
    method: 'POST',
    cookie: bob.cookie,
    payload: { variantId: 'bob-fork', name: 'Bob Fork' },
  });
  assert.equal(forked.status, 200);
  assert.equal(forked.json.variantId, 'bob-fork');
  assert.equal(forked.json.visibility, 'private');
  assert.equal(forked.json.editable, true);

  const bobMine = await jsonRequest(app, env, '/v1/variants?owner=me', { cookie: bob.cookie });
  assert.equal(bobMine.status, 200);
  assert.equal(bobMine.json.entries.length, 1);
  assert.equal(bobMine.json.entries[0].variantId, 'bob-fork');

  const bobPublicOnly = await jsonRequest(app, env, '/v1/variants?owner=public', { cookie: bob.cookie });
  assert.equal(bobPublicOnly.status, 200);
  assert.equal(bobPublicOnly.json.entries.length, 1);
  assert.equal(bobPublicOnly.json.entries[0].variantId, 'alice-variant');

  const unsubscribed = await jsonRequest(app, env, '/v1/variants/alice-variant/subscribe', {
    method: 'DELETE',
    cookie: bob.cookie,
  });
  assert.equal(unsubscribed.status, 200);

  const removed = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'DELETE',
    cookie: alice.cookie,
  });
  assert.equal(removed.status, 200);

  const publicAfterDelete = await jsonRequest(app, env, '/v1/variants?owner=public');
  assert.equal(publicAfterDelete.status, 200);
  assert.equal(publicAfterDelete.json.entries.length, 0);
});

test('component asset lifecycle validates session envelope and visibility rules', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });
  const bob = await createVerifiedSession(app, env, {
    username: 'bob',
    email: 'bob@example.com',
    displayName: 'Bob',
  });

  const invalid = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: {
      record: {
        componentId: 'bad-component',
        name: 'Bad',
        description: '',
        tags: [],
        schemaVersion: 'bad',
        content: { schemaVersion: 'bad', layout: {} },
      },
    },
  });
  assert.equal(invalid.status, 400);

  const created = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'component-a', name: 'Published Session', visibility: 'public' }),
  });
  assert.equal(created.status, 200);
  assert.equal(created.json.componentId, 'component-a');
  assert.equal(created.json.schemaVersion, 'f8studio-session/1');
  const componentVariantDetails = await env.DB.prepare(
    'SELECT asset_id FROM variant_details WHERE asset_id = ?',
  )
    .bind('component-a')
    .first();
  assert.equal(componentVariantDetails, null);

  const publicList = await jsonRequest(app, env, '/v1/components?owner=public');
  assert.equal(publicList.status, 200);
  assert.equal(publicList.json.entries.length, 1);
  assert.equal(publicList.json.entries[0].componentId, 'component-a');
  assert.equal(publicList.json.entries[0].name, 'Published Session');
  assert.equal(publicList.json.entries[0].hasContent, true);

  const componentListSearch = await jsonRequest(app, env, '/v1/components?owner=public');
  assert.equal(componentListSearch.status, 200);
  assert.equal(componentListSearch.json.entries[0].componentId, 'component-a');
  assert.equal(componentListSearch.json.entries[0].schemaVersion, 'f8studio-session/1');
  assert.equal(Object.hasOwn(componentListSearch.json.entries[0], 'variantKind'), false);

  const subscribed = await jsonRequest(app, env, '/v1/components/component-a/subscribe', {
    method: 'POST',
    cookie: bob.cookie,
  });
  assert.equal(subscribed.status, 200);
  assert.equal(subscribed.json.subscribed, true);
  assert.equal(subscribed.json.editable, false);

  const history1 = await jsonRequest(app, env, '/v1/components/component-a/versions', { cookie: alice.cookie });
  assert.equal(history1.status, 200);
  assert.equal(history1.json.versions.length, 1);
  assert.equal(history1.json.versions[0].componentId, 'component-a');

  const updated = await jsonRequest(app, env, '/v1/components/component-a', {
    method: 'PUT',
    cookie: alice.cookie,
    payload: componentPayload({
      componentId: 'component-a',
      name: 'Published Session v2',
      visibility: 'public',
      revision: created.json.revision,
    }),
  });
  assert.equal(updated.status, 200);
  assert.equal(updated.json.revision, 'r2');

  const componentHeadBeforeMetaPatch = await env.DB.prepare(
    `SELECT latest_version_number, latest_revision, updated_at, schema_version
     FROM asset_heads
     WHERE asset_id = ?`,
  )
    .bind('component-a')
    .first();

  const componentMetadataPatched = await jsonRequest(app, env, '/v1/components/component-a/meta', {
    method: 'PATCH',
    cookie: alice.cookie,
    payload: {
      name: 'Published Session Metadata',
      description: 'Metadata only component update',
      tags: ['meta', 'component'],
    },
  });
  assert.equal(componentMetadataPatched.status, 200);
  assert.equal(componentMetadataPatched.json.name, 'Published Session Metadata');
  assert.equal(componentMetadataPatched.json.description, 'Metadata only component update');
  assert.deepEqual(componentMetadataPatched.json.tags, ['meta', 'component']);
  assert.equal(componentMetadataPatched.json.latestVersionNumber, 2);
  assert.equal(componentMetadataPatched.json.latestRevision, 'r2');

  const componentHeadAfterMetaPatch = await env.DB.prepare(
    `SELECT latest_version_number, latest_revision, updated_at, schema_version
     FROM asset_heads
     WHERE asset_id = ?`,
  )
    .bind('component-a')
    .first();
  assert.equal(Number(componentHeadAfterMetaPatch?.latest_version_number ?? 0), 2);
  assert.equal(String(componentHeadAfterMetaPatch?.latest_revision || ''), 'r2');
  assert.equal(String(componentHeadAfterMetaPatch?.schema_version || ''), 'f8studio-session/1');
  assert.notEqual(
    String(componentHeadAfterMetaPatch?.updated_at || ''),
    String(componentHeadBeforeMetaPatch?.updated_at || ''),
  );

  const componentVersionsAfterMetaPatch = await jsonRequest(app, env, '/v1/components/component-a/versions', { cookie: alice.cookie });
  assert.equal(componentVersionsAfterMetaPatch.status, 200);
  assert.equal(componentVersionsAfterMetaPatch.json.versions.length, 2);

  const forbiddenComponentMetadataPatch = await jsonRequest(app, env, '/v1/components/component-a/meta', {
    method: 'PATCH',
    cookie: bob.cookie,
    payload: {
      name: 'Bob Component Edit',
      description: 'forbidden',
      tags: ['forbidden'],
    },
  });
  assert.equal(forbiddenComponentMetadataPatch.status, 403);

  const oldVersion = await jsonRequest(app, env, '/v1/components/component-a/versions/1', { cookie: bob.cookie });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.componentId, 'component-a');
  assert.equal(oldVersion.json.hasContent, true);
  const oldVersionContent = await jsonRequest(app, env, '/v1/components/component-a/versions/1/content', { cookie: bob.cookie });
  assert.equal(oldVersionContent.status, 200);
  assert.equal(oldVersionContent.json.record.name, 'Published Session Metadata');

  const forbidden = await jsonRequest(app, env, '/v1/components/component-a', {
    method: 'PUT',
    cookie: bob.cookie,
    payload: componentPayload({
      componentId: 'component-a',
      name: 'Bob Edit',
      visibility: 'public',
      revision: updated.json.revision,
    }),
  });
  assert.equal(forbidden.status, 403);

  const forked = await jsonRequest(app, env, '/v1/components/component-a/fork', {
    method: 'POST',
    cookie: bob.cookie,
    payload: { componentId: 'component-b', name: 'Bob Session Copy' },
  });
  assert.equal(forked.status, 200);
  assert.equal(forked.json.componentId, 'component-b');
  assert.equal(forked.json.visibility, 'private');
});

test('component list and search do not depend on variant details table', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const created = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'component-no-vd', name: 'Standalone Component', visibility: 'public' }),
  });
  assert.equal(created.status, 200);

  const admin = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(admin.status, 200);

  await env.DB.prepare('DROP TABLE variant_details').run();

  const listed = await jsonRequest(app, env, '/v1/components?visibility=public&owner=public');
  assert.equal(listed.status, 200);
  assert.equal(listed.json.entries.length, 1);
  assert.equal(listed.json.entries[0].componentId, 'component-no-vd');

  const detail = await jsonRequest(app, env, '/v1/components/component-no-vd');
  assert.equal(detail.status, 200);
  assert.equal(detail.json.componentId, 'component-no-vd');

  const content = await jsonRequest(app, env, '/v1/components/component-no-vd/content');
  assert.equal(content.status, 200);
  assert.equal(content.json.componentId, 'component-no-vd');
  assert.equal(content.json.record.name, 'Standalone Component');

  const searched = await jsonRequest(app, env, '/v1/components?visibility=public&owner=public&q=standalone');
  assert.equal(searched.status, 200);
  assert.equal(searched.json.entries.length, 1);
  assert.equal(searched.json.entries[0].componentId, 'component-no-vd');

  const managedList = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/components`, {
    cookie: admin.cookie,
  });
  assert.equal(managedList.status, 200);
  assert.equal(managedList.json.entries.length, 1);
  assert.equal(managedList.json.entries[0].assetId, 'component-no-vd');

  const managedDetail = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/components/component-no-vd`, {
    cookie: admin.cookie,
  });
  assert.equal(managedDetail.status, 200);
  assert.equal(managedDetail.json.assetId, 'component-no-vd');
});

test('component content endpoint reads canonical stored session payload and rejects legacy wrapped blobs', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const created = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'component-canonical', name: 'Canonical Component', visibility: 'public' }),
  });
  assert.equal(created.status, 200);

  const storedVersion = await env.DB.prepare(
    `SELECT content
     FROM asset_versions
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind('component-canonical')
    .first();
  const storedContent = JSON.parse(gunzipSync(Buffer.from(storedVersion.content)).toString('utf-8'));
  assert.equal(storedContent.schemaVersion, 'f8studio-session/1');
  assert.ok(storedContent.layout);
  assert.equal(storedContent.record, undefined);
  assert.equal(storedContent.name, undefined);

  const canonicalSessionPayload = JSON.stringify({
    schemaVersion: 'f8studio-session/1',
    layout: {
      nodes: {
        canonicalNode: {
          id: 'canonicalNode',
          name: 'Canonical Node',
          pos: [0, 0],
        },
      },
      connections: [],
    },
  });
  await env.DB.prepare(
    `UPDATE asset_versions
     SET content = ?
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind(gzipSync(Buffer.from(canonicalSessionPayload)), 'component-canonical')
    .run();

  const contentResponse = await jsonRequest(app, env, '/v1/components/component-canonical/content');
  assert.equal(contentResponse.status, 200);
  assert.equal(contentResponse.json.record.componentId, 'component-canonical');
  assert.equal(contentResponse.json.record.name, 'Canonical Component');
  assert.equal(contentResponse.json.record.description, 'published session');
  assert.equal(contentResponse.json.record.content.layout.nodes.canonicalNode.name, 'Canonical Node');

  const directRecordBlob = JSON.stringify({
    componentId: 'component-canonical',
    name: 'Direct Record Blob',
    description: 'should be rejected',
    tags: ['invalid'],
    schemaVersion: 'f8studio-session/1',
    content: {
      schemaVersion: 'f8studio-session/1',
      layout: {
        nodes: {},
        connections: [],
      },
    },
    createdAt: '2026-04-01T00:00:00.000Z',
    updatedAt: '2026-04-02T00:00:00.000Z',
  });
  await env.DB.prepare(
    `UPDATE asset_versions
     SET content = ?
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind(gzipSync(Buffer.from(directRecordBlob)), 'component-canonical')
    .run();

  const directRecordContent = await jsonRequest(app, env, '/v1/components/component-canonical/content');
  assert.equal(directRecordContent.status, 400);
  assert.equal(directRecordContent.json.message, 'stored component content must be the canonical session payload { schemaVersion, layout }');

  const legacyEnvelopeBlob = JSON.stringify({
    componentId: 'component-canonical',
    assetType: 'component',
    versionNumber: 1,
    revision: 'r1',
    record: {
      componentId: 'component-canonical',
      name: 'Envelope Record Blob',
      description: 'legacy envelope',
      tags: ['legacy'],
      schemaVersion: 'f8studio-session/1',
      content: {
        schemaVersion: 'f8studio-session/1',
        layout: {
          nodes: {
            fromEnvelope: {
              id: 'fromEnvelope',
              name: 'Envelope Node',
              pos: [10, 20],
            },
          },
          connections: [],
        },
      },
      createdAt: '2026-04-01T00:00:00.000Z',
      updatedAt: '2026-04-02T00:00:00.000Z',
    },
  });
  await env.DB.prepare(
    `UPDATE asset_versions
     SET content = ?
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind(gzipSync(Buffer.from(legacyEnvelopeBlob)), 'component-canonical')
    .run();

  const envelopeContent = await jsonRequest(app, env, '/v1/components/component-canonical/content');
  assert.equal(envelopeContent.status, 400);
  assert.equal(envelopeContent.json.message, 'stored component content must be the canonical session payload { schemaVersion, layout }');
});

test('component content endpoint decodes canonical gzip blobs from buffer-like D1 rows', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const created = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'component-buffer-shape', name: 'Buffer Shape', visibility: 'public' }),
  });
  assert.equal(created.status, 200);

  wrapBlobRowsAsDataArrays(env.DB);

  const contentResponse = await jsonRequest(app, env, '/v1/components/component-buffer-shape/content');
  assert.equal(contentResponse.status, 200);
  assert.equal(contentResponse.json.record.componentId, 'component-buffer-shape');
  assert.equal(contentResponse.json.record.content.schemaVersion, 'f8studio-session/1');
  assert.ok(contentResponse.json.record.content.layout);
});

test('variant content endpoint reads canonical raw spec and rejects wrapped blobs', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const created = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'variant-canonical', name: 'Canonical Variant', visibility: 'public' }),
  });
  assert.equal(created.status, 200);

  const rawSpecContent = await jsonRequest(app, env, '/v1/variants/variant-canonical/content');
  assert.equal(rawSpecContent.status, 200);
  assert.equal(rawSpecContent.json.record.variantId, 'variant-canonical');
  assert.equal(rawSpecContent.json.record.spec.label, 'Canonical Variant');

  const fullRecordBlob = JSON.stringify({
    variantId: 'variant-canonical',
    kind: 'operator',
    baseNodeType: 'svc.base.op',
    serviceClass: 'svc.test',
    operatorClass: 'op.test',
    name: 'Legacy Variant Record',
    description: 'legacy record blob',
    tags: ['legacy'],
    spec: {
      label: 'Legacy Variant Spec',
      fields: ['a', 'b'],
    },
    createdAt: '2026-04-01T00:00:00.000Z',
    updatedAt: '2026-04-02T00:00:00.000Z',
  });
  await env.DB.prepare(
    `UPDATE asset_versions
     SET content = ?
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind(gzipSync(Buffer.from(fullRecordBlob)), 'variant-canonical')
    .run();

  const fullRecordContent = await jsonRequest(app, env, '/v1/variants/variant-canonical/content');
  assert.equal(fullRecordContent.status, 400);
  assert.equal(fullRecordContent.json.message, 'stored variant content must be the raw spec JSON object without record or envelope metadata');

  const envelopeBlob = JSON.stringify({
    variantId: 'variant-canonical',
    assetType: 'variant',
    versionNumber: 1,
    revision: 'r1',
    record: {
      variantId: 'variant-canonical',
      kind: 'operator',
      baseNodeType: 'svc.base.op',
      serviceClass: 'svc.test',
      operatorClass: 'op.test',
      name: 'Envelope Variant Record',
      description: 'legacy envelope',
      tags: ['envelope'],
      spec: {
        label: 'Envelope Variant Spec',
        knobs: 4,
      },
      createdAt: '2026-04-01T00:00:00.000Z',
      updatedAt: '2026-04-02T00:00:00.000Z',
    },
  });
  await env.DB.prepare(
    `UPDATE asset_versions
     SET content = ?
     WHERE asset_id = ? AND version_number = 1`,
  )
    .bind(gzipSync(Buffer.from(envelopeBlob)), 'variant-canonical')
    .run();

  const envelopeContent = await jsonRequest(app, env, '/v1/variants/variant-canonical/content');
  assert.equal(envelopeContent.status, 400);
  assert.equal(envelopeContent.json.message, 'stored variant content must be the raw spec JSON object without record or envelope metadata');
});

test('management APIs support Better Auth backed user and asset management', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const managementLogin = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(managementLogin.status, 200);
  assert.ok(managementLogin.cookie);

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const createdByAlice = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'alice-private-asset', name: 'Alice Private Asset', visibility: 'private' }),
  });
  assert.equal(createdByAlice.status, 200);

  const nonAdminDenied = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users`, { cookie: alice.cookie });
  assert.equal(nonAdminDenied.status, 403);

  const managementUsers = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users`, { cookie: managementLogin.cookie });
  assert.equal(managementUsers.status, 200);
  assert.equal(managementUsers.json.entries.length >= 2, true);

  const managementCreatesUser = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users`, {
    method: 'POST',
    cookie: managementLogin.cookie,
    payload: {
      username: 'ops',
      email: 'ops@example.com',
      password: TEST_PASSWORD,
      displayName: 'Ops',
      role: 'readonly',
    },
  });
  assert.equal(managementCreatesUser.status, 200);
  const opsUserId = String(managementCreatesUser.json.userId);
  assert.equal(managementCreatesUser.json.role, 'readonly');

  const managementUpdatesUser = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${opsUserId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      username: 'ops_team',
      displayName: 'Ops Team',
      role: 'user',
      password: TEST_PASSWORD_2,
    },
  });
  assert.equal(managementUpdatesUser.status, 200);
  assert.equal(managementUpdatesUser.json.username, 'ops_team');
  assert.equal(managementUpdatesUser.json.displayName, 'Ops Team');
  assert.equal(managementUpdatesUser.json.role, 'user');

  const managementUsersAfterUpdate = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementUsersAfterUpdate.status, 200);
  assert.equal(
    managementUsersAfterUpdate.json.entries.some((entry) => entry.userId === opsUserId && entry.role === 'user'),
    true,
  );

  const usernameConflict = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${opsUserId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      username: 'alice',
    },
  });
  assert.equal(usernameConflict.status, 409);

  const managementViewsAliceAssets = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants?ownerUserId=${encodeURIComponent(alice.userId)}`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementViewsAliceAssets.status, 200);
  assert.equal(managementViewsAliceAssets.json.entries.length, 1);
  assert.equal(managementViewsAliceAssets.json.entries[0].assetId, 'alice-private-asset');

  const managementListsAssets = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementListsAssets.status, 200);
  assert.equal(managementListsAssets.json.entries.length >= 1, true);
  assert.equal(managementListsAssets.json.entries[0].variantKind, 'operator');

  const managementVariantDetail = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants/alice-private-asset`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementVariantDetail.status, 200);
  assert.equal(managementVariantDetail.json.assetId, 'alice-private-asset');
  assert.equal(managementVariantDetail.json.variantKind, 'operator');
  assert.equal(managementVariantDetail.json.baseNodeType, 'svc.base.op');

  const managementChangesVisibility = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants/alice-private-asset`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: { visibility: 'public' },
  });
  assert.equal(managementChangesVisibility.status, 200);
  assert.equal(managementChangesVisibility.json.visibility, 'public');

  const managementDeletesAsset = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants/alice-private-asset`, {
    method: 'DELETE',
    cookie: managementLogin.cookie,
  });
  assert.equal(managementDeletesAsset.status, 200);

  const hiddenFromDefaultManagementList = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(hiddenFromDefaultManagementList.status, 200);
  assert.equal(
    hiddenFromDefaultManagementList.json.entries.some((entry) => entry.assetId === 'alice-private-asset'),
    false,
  );

  const includeDeleted = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants?includeDeleted=true`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(includeDeleted.status, 200);
  assert.equal(
    includeDeleted.json.entries.some((entry) => entry.assetId === 'alice-private-asset' && entry.deletedAt !== null),
    true,
  );

  const managementRestoresAsset = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants/alice-private-asset`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: { restore: true },
  });
  assert.equal(managementRestoresAsset.status, 200);
  assert.equal(managementRestoresAsset.json.deletedAt, null);

  const managementLocksAliceUploads = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${alice.userId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      role: 'readonly',
    },
  });
  assert.equal(managementLocksAliceUploads.status, 200);
  assert.equal(managementLocksAliceUploads.json.role, 'readonly');

  const aliceBlockedUpload = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'alice-blocked-upload', name: 'Alice Blocked Upload', visibility: 'private' }),
  });
  assert.equal(aliceBlockedUpload.status, 403);
  assert.equal(aliceBlockedUpload.json.message, 'upload permission required');

  const managementUnlocksAliceUploads = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${alice.userId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      role: 'user',
    },
  });
  assert.equal(managementUnlocksAliceUploads.status, 200);
  assert.equal(managementUnlocksAliceUploads.json.role, 'user');

  const aliceAllowedUpload = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'alice-allowed-upload', name: 'Alice Allowed Upload', visibility: 'private' }),
  });
  assert.equal(aliceAllowedUpload.status, 200);

  const deleteAliceBlocked = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${alice.userId}`, {
    method: 'DELETE',
    cookie: managementLogin.cookie,
  });
  assert.equal(deleteAliceBlocked.status, 409);

  const managementSelf = String(managementLogin.json.user.id);
  const selfDelete = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${managementSelf}`, {
    method: 'DELETE',
    cookie: managementLogin.cookie,
  });
  assert.equal(selfDelete.status, 400);

  const deleteOps = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${opsUserId}`, {
    method: 'DELETE',
    cookie: managementLogin.cookie,
  });
  assert.equal(deleteOps.status, 200);
});

test('site settings default to registration disabled and management can enable registration', async (t) => {
  const env = createEnv({ allowUserRegistration: false });
  t.after(() => env.DB.close());
  const app = createApp();

  const initialSettings = await jsonRequest(app, env, '/v1/site-settings');
  assert.equal(initialSettings.status, 200);
  assert.equal(initialSettings.json.allowUserRegistration, false);

  const blockedSignUp = await signUpUser(app, env, {
    username: 'blocked_user',
    email: 'blocked@example.com',
    displayName: 'Blocked User',
  });
  assert.equal(blockedSignUp.status, 403);
  assert.equal(blockedSignUp.json.message, 'new user registration is disabled');

  const managementLogin = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(managementLogin.status, 200);

  const managementSettings = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/site-settings`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementSettings.status, 200);
  assert.equal(managementSettings.json.allowUserRegistration, false);

  const enabledSettings = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/site-settings`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      allowUserRegistration: true,
    },
  });
  assert.equal(enabledSettings.status, 200);
  assert.equal(enabledSettings.json.allowUserRegistration, true);

  const allowedSignUp = await signUpUser(app, env, {
    username: 'allowed_user',
    email: 'allowed@example.com',
    displayName: 'Allowed User',
  });
  assert.equal(allowedSignUp.status, 200);
  assert.equal(allowedSignUp.json.user.username, 'allowed_user');
});

test('management can permanently purge all assets', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const managementLogin = await signInUser(app, env, {
    username: 'admin',
  });
  assert.equal(managementLogin.status, 200);

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const createdVariant = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    cookie: alice.cookie,
    payload: variantPayload({ variantId: 'alice-variant', name: 'Alice Variant', visibility: 'public' }),
  });
  assert.equal(createdVariant.status, 200);

  const createdComponent = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'alice-component', name: 'Alice Component', visibility: 'public' }),
  });
  assert.equal(createdComponent.status, 200);

  const variantUpdate = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    cookie: alice.cookie,
    payload: {
      ...variantPayload({ variantId: 'alice-variant', name: 'Alice Variant v2', visibility: 'public' }),
      revision: 'r1',
    },
  });
  assert.equal(variantUpdate.status, 200);

  const subscribeComponent = await jsonRequest(app, env, '/v1/components/alice-component/subscribe', {
    method: 'POST',
    cookie: managementLogin.cookie,
  });
  assert.equal(subscribeComponent.status, 200);

  const subscribeVariant = await jsonRequest(app, env, '/v1/variants/alice-variant/subscribe', {
    method: 'POST',
    cookie: managementLogin.cookie,
  });
  assert.equal(subscribeVariant.status, 200);

  const rejectedPurge = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets/purge-all`, {
    method: 'POST',
    cookie: managementLogin.cookie,
    payload: {
      confirmationText: 'nope',
    },
  });
  assert.equal(rejectedPurge.status, 400);

  const purge = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets/purge-all`, {
    method: 'POST',
    cookie: managementLogin.cookie,
    payload: {
      confirmationText: 'DELETE ALL ASSETS',
    },
  });
  assert.equal(purge.status, 200);
  assert.equal(purge.json.deletedAssets, 2);
  assert.equal(purge.json.deletedAssetVersions, 3);
  assert.equal(purge.json.deletedAssetSubscriptions, 2);
  assert.equal(purge.json.deletedVariantDetails, 1);

  const managedVariants = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/variants?includeDeleted=true`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managedVariants.status, 200);
  assert.deepEqual(managedVariants.json.entries, []);

  const managedComponents = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/components?includeDeleted=true`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managedComponents.status, 200);
  assert.deepEqual(managedComponents.json.entries, []);

  const publicVariants = await jsonRequest(app, env, '/v1/variants?owner=public');
  assert.equal(publicVariants.status, 200);
  assert.deepEqual(publicVariants.json.entries, []);

  const publicComponents = await jsonRequest(app, env, '/v1/components?owner=public');
  assert.equal(publicComponents.status, 200);
  assert.deepEqual(publicComponents.json.entries, []);

  const userDirectory = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(userDirectory.status, 200);
  const aliceEntry = userDirectory.json.entries.find((entry) => entry.username === 'alice');
  assert.equal(aliceEntry.assetCount, 0);
});

test('console entry page is served as html', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const rootResponse = await app.fetch(new Request('http://worker.test/'), env, {});
  assert.equal(rootResponse.status, 302);
  assert.equal(rootResponse.headers.get('Location'), `http://worker.test${CONSOLE_BASE_PATH}/`);

  const response = await app.fetch(new Request(`http://worker.test${CONSOLE_BASE_PATH}`), env, {});
  assert.equal(response.status, 200);
  assert.match(response.headers.get('Content-Type') || '', /text\/html/);

  const html = await response.text();
  assert.match(html, /Feel8 Asset Cloud/);
});

test('auth helper pages are served as html', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const verifyResponse = await app.fetch(new Request(`http://worker.test${CONSOLE_BASE_PATH}/verify-email?token=test-token`), env, {});
  assert.equal(verifyResponse.status, 200);
  assert.match(verifyResponse.headers.get('Content-Type') || '', /text\/html/);
  const verifyHtml = await verifyResponse.text();
  assert.match(verifyHtml, /Feel8 Asset Cloud/);

  const resetResponse = await app.fetch(new Request(`http://worker.test${CONSOLE_BASE_PATH}/reset-password?token=test-token`), env, {});
  assert.equal(resetResponse.status, 200);
  assert.match(resetResponse.headers.get('Content-Type') || '', /text\/html/);
  const resetHtml = await resetResponse.text();
  assert.match(resetHtml, /Feel8 Asset Cloud/);
});

test('worker leaves typed list responses uncompressed by default', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());

  const listRequest = new Request('http://worker.test/v1/components?owner=public', {
    headers: {
      'Accept-Encoding': 'gzip',
    },
  });
  const listResponse = await worker.fetch(listRequest, env, {});
  assert.equal(listResponse.status, 200);
  assert.equal(listResponse.headers.get('Content-Encoding'), null);
});

test('worker gzips large asset payload responses by default and leaves auth/list uncompressed', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const created = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    cookie: alice.cookie,
    payload: componentPayload({ componentId: 'component-gzip', name: 'Compressed Component', visibility: 'public' }),
  });
  assert.equal(created.status, 200);

  const listRequest = new Request('http://worker.test/v1/components?owner=public', {
    headers: {
      'Accept-Encoding': 'gzip',
    },
  });
  const listResponse = await worker.fetch(listRequest, env, {});
  assert.equal(listResponse.status, 200);
  assert.equal(listResponse.headers.get('Content-Encoding'), null);

  const detailRequest = new Request('http://worker.test/v1/components/component-gzip/content', {
    headers: {
      'Accept-Encoding': 'gzip',
      cookie: alice.cookie,
    },
  });
  const detailResponse = await worker.fetch(detailRequest, env, {});
  assert.equal(detailResponse.status, 200);
  assert.equal(detailResponse.headers.get('Content-Encoding'), 'gzip');
  assert.match(detailResponse.headers.get('Cache-Control') || '', /(?:^|,\s*)no-transform(?:,|$)/);
  assert.match(detailResponse.headers.get('Content-Type') || '', /application\/json/);
  const compressedBody = Buffer.from(await detailResponse.arrayBuffer());
  const detailPayload = JSON.parse(gunzipSync(compressedBody).toString('utf-8'));
  assert.equal(detailPayload.componentId, 'component-gzip');

  const sessionRequest = new Request('http://worker.test/api/auth/get-session', {
    headers: {
      'Accept-Encoding': 'gzip',
      cookie: alice.cookie,
    },
  });
  const sessionResponse = await worker.fetch(sessionRequest, env, {});
  assert.equal(sessionResponse.status, 200);
  assert.equal(sessionResponse.headers.get('Content-Encoding'), null);
});

test('worker can accept gzip-compressed asset JSON request bodies', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await createVerifiedSession(app, env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const compressedPayload = gzipSync(Buffer.from(JSON.stringify(
    componentPayload({ componentId: 'component-gzip-upload', name: 'Compressed Upload', visibility: 'private' }),
  )));

  const createRequest = new Request('http://worker.test/v1/components', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Encoding': 'gzip',
      cookie: alice.cookie,
    },
    body: compressedPayload,
  });
  const createResponse = await worker.fetch(createRequest, env, {});
  assert.equal(createResponse.status, 200);
  const createJson = JSON.parse(await createResponse.text());
  assert.equal(createJson.componentId, 'component-gzip-upload');
});

test('worker rejects mismatched gzip request body headers', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());

  const alice = await createVerifiedSession(createApp(), env, {
    username: 'alice',
    email: 'alice@example.com',
    displayName: 'Alice',
  });

  const payloadJson = JSON.stringify(
    componentPayload({ componentId: 'component-invalid-gzip', name: 'Invalid Gzip', visibility: 'private' }),
  );
  const gzippedPayload = gzipSync(Buffer.from(payloadJson));

  const missingHeaderResponse = await worker.fetch(new Request('http://worker.test/v1/components', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      cookie: alice.cookie,
    },
    body: gzippedPayload,
  }), env, {});
  assert.equal(missingHeaderResponse.status, 400);
  assert.equal((await missingHeaderResponse.json()).message, 'request body must be a JSON object');

  const invalidGzipHeaderResponse = await worker.fetch(new Request('http://worker.test/v1/components', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Encoding': 'gzip',
      cookie: alice.cookie,
    },
    body: payloadJson,
  }), env, {});
  assert.equal(invalidGzipHeaderResponse.status, 400);
  assert.equal((await invalidGzipHeaderResponse.json()).message, 'request body gzip decompression failed');
});

test('worker only gzips all /v1 json responses when explicitly enabled and still leaves /api/auth uncompressed', async (t) => {
  const env = createEnv({
    ENABLE_API_JSON_GZIP: 'true',
  });
  t.after(() => env.DB.close());

  const listRequest = new Request('http://worker.test/v1/components?owner=public', {
    headers: {
      'Accept-Encoding': 'gzip',
    },
  });
  const listResponse = await worker.fetch(listRequest, env, {});
  assert.equal(listResponse.status, 200);
  assert.equal(listResponse.headers.get('Content-Encoding'), 'gzip');
  assert.match(listResponse.headers.get('Cache-Control') || '', /(?:^|,\s*)no-transform(?:,|$)/);
  assert.match(listResponse.headers.get('Content-Type') || '', /application\/json/);
  const compressedBody = Buffer.from(await listResponse.arrayBuffer());
  const listPayload = JSON.parse(gunzipSync(compressedBody).toString('utf-8'));
  assert.ok(Array.isArray(listPayload.entries));

  const sessionRequest = new Request('http://worker.test/api/auth/get-session', {
    headers: {
      'Accept-Encoding': 'gzip',
    },
  });
  const sessionResponse = await worker.fetch(sessionRequest, env, {});
  assert.equal(sessionResponse.status, 200);
  assert.equal(sessionResponse.headers.get('Content-Encoding'), null);
});
