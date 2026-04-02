import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { createApp } from '../src/app.js';
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

function createEnv(overrides = {}) {
  return {
    DB: createSqliteD1Database({ migrationsSql }),
    BETTER_AUTH_SECRET: TEST_AUTH_SECRET,
    BOOTSTRAP_ADMIN_USERNAME: 'admin',
    BOOTSTRAP_ADMIN_DISPLAY_NAME: 'Administrator',
    BOOTSTRAP_ADMIN_PASSWORD: TEST_PASSWORD,
    BOOTSTRAP_ADMIN_EMAIL: 'admin@example.com',
    EXPOSE_DEBUG_AUTH_LINKS: 'true',
    ...overrides,
  };
}

async function jsonRequest(app, env, pathname, { method, payload, cookie } = {}) {
  const headers = {};
  if (payload !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  if (cookie) {
    headers.cookie = cookie;
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
  return String(url.searchParams.get('token') || '');
}

function responseCookie(headers) {
  const setCookie = headers.get('set-cookie');
  return setCookie ? String(setCookie).split(';')[0] : '';
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
  t.after(() => env.DB.close());
  const app = createApp();

  const providers = await jsonRequest(app, env, '/v1/auth/providers');
  assert.equal(providers.status, 200);
  assert.equal(providers.json.google, false);

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
  assert.equal(created.json.assetType, 'variant');
  assert.equal(created.json.revision, 'r1');
  assert.equal(created.json.editable, true);

  const publicSearchBefore = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=public');
  assert.equal(publicSearchBefore.status, 200);
  assert.equal(publicSearchBefore.json.entries.length, 0);

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

  const publicSearchAfter = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=public&q=alice');
  assert.equal(publicSearchAfter.status, 200);
  assert.equal(publicSearchAfter.json.entries.length, 1);

  const subscribed = await jsonRequest(app, env, '/v1/variants/alice-variant/subscribe', {
    method: 'POST',
    cookie: bob.cookie,
  });
  assert.equal(subscribed.status, 200);
  assert.equal(subscribed.json.subscribed, true);
  assert.equal(subscribed.json.editable, false);

  const subscribedSearch = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=subscribed', {
    cookie: bob.cookie,
  });
  assert.equal(subscribedSearch.status, 200);
  assert.equal(subscribedSearch.json.entries.length, 1);
  assert.equal(subscribedSearch.json.entries[0].assetId, 'alice-variant');

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
  assert.equal(history.json.versions[0].versionNumber, 2);

  const oldVersion = await jsonRequest(app, env, '/v1/variants/alice-variant/versions/1', { cookie: alice.cookie });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.record.name, 'Alice Private');

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

  const forked = await jsonRequest(app, env, '/v1/variants/alice-variant/fork', {
    method: 'POST',
    cookie: bob.cookie,
    payload: { variantId: 'bob-fork', name: 'Bob Fork' },
  });
  assert.equal(forked.status, 200);
  assert.equal(forked.json.assetId, 'bob-fork');
  assert.equal(forked.json.visibility, 'private');
  assert.equal(forked.json.editable, true);

  const bobMine = await jsonRequest(app, env, '/v1/variants?owner=me', { cookie: bob.cookie });
  assert.equal(bobMine.status, 200);
  assert.equal(bobMine.json.entries.length, 1);
  assert.equal(bobMine.json.entries[0].assetId, 'bob-fork');

  const bobPublicOnly = await jsonRequest(app, env, '/v1/variants?owner=public', { cookie: bob.cookie });
  assert.equal(bobPublicOnly.status, 200);
  assert.equal(bobPublicOnly.json.entries.length, 1);
  assert.equal(bobPublicOnly.json.entries[0].assetId, 'alice-variant');

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

  const publicAfterDelete = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=public');
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
  assert.equal(created.json.record.schemaVersion, 'f8studio-session/1');

  const publicList = await jsonRequest(app, env, '/v1/components?owner=public');
  assert.equal(publicList.status, 200);
  assert.equal(publicList.json.entries.length, 1);

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

  const oldVersion = await jsonRequest(app, env, '/v1/components/component-a/versions/1', { cookie: bob.cookie });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.record.name, 'Published Session');

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
  assert.equal(forked.json.assetId, 'component-b');
  assert.equal(forked.json.visibility, 'private');
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
      isAdmin: true,
    },
  });
  assert.equal(managementCreatesUser.status, 200);
  const opsUserId = String(managementCreatesUser.json.userId);

  const managementUpdatesUser = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${opsUserId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      username: 'ops_team',
      displayName: 'Ops Team',
      isAdmin: false,
      password: TEST_PASSWORD_2,
    },
  });
  assert.equal(managementUpdatesUser.status, 200);
  assert.equal(managementUpdatesUser.json.username, 'ops_team');
  assert.equal(managementUpdatesUser.json.displayName, 'Ops Team');
  assert.equal(managementUpdatesUser.json.isAdmin, false);

  const usernameConflict = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${opsUserId}`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: {
      username: 'alice',
    },
  });
  assert.equal(usernameConflict.status, 409);

  const managementViewsAliceAssets = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/users/${alice.userId}/assets`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementViewsAliceAssets.status, 200);
  assert.equal(managementViewsAliceAssets.json.entries.length, 1);
  assert.equal(managementViewsAliceAssets.json.entries[0].assetId, 'alice-private-asset');

  const managementListsAssets = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets?assetType=variant`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(managementListsAssets.status, 200);
  assert.equal(managementListsAssets.json.entries.length >= 1, true);

  const managementChangesVisibility = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets/alice-private-asset`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: { visibility: 'public' },
  });
  assert.equal(managementChangesVisibility.status, 200);
  assert.equal(managementChangesVisibility.json.visibility, 'public');

  const managementDeletesAsset = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets/alice-private-asset`, {
    method: 'DELETE',
    cookie: managementLogin.cookie,
  });
  assert.equal(managementDeletesAsset.status, 200);

  const hiddenFromDefaultManagementList = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets?assetType=variant`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(hiddenFromDefaultManagementList.status, 200);
  assert.equal(
    hiddenFromDefaultManagementList.json.entries.some((entry) => entry.assetId === 'alice-private-asset'),
    false,
  );

  const includeDeleted = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets?assetType=variant&includeDeleted=true`, {
    cookie: managementLogin.cookie,
  });
  assert.equal(includeDeleted.status, 200);
  assert.equal(
    includeDeleted.json.entries.some((entry) => entry.assetId === 'alice-private-asset' && entry.deletedAt !== null),
    true,
  );

  const managementRestoresAsset = await jsonRequest(app, env, `${MANAGEMENT_API_BASE_PATH}/assets/alice-private-asset`, {
    method: 'PUT',
    cookie: managementLogin.cookie,
    payload: { restore: true },
  });
  assert.equal(managementRestoresAsset.status, 200);
  assert.equal(managementRestoresAsset.json.deletedAt, null);

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
