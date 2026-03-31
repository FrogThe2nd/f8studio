import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { createApp } from '../src/app.js';
import { createSqliteD1Database } from '../test_support/sqlite_d1_adapter.js';

const migrationsSql = fs.readFileSync(
  path.join(import.meta.dirname, '..', 'migrations', '0001_init.sql'),
  'utf8',
);

function createEnv() {
  return {
    DB: createSqliteD1Database({ migrationsSql }),
    JWT_SECRET: 'test-secret',
    JWT_ISSUER: 'feel8-asset-cloud',
    BOOTSTRAP_ADMIN_USERNAME: 'admin',
    BOOTSTRAP_ADMIN_DISPLAY_NAME: 'Administrator',
    BOOTSTRAP_ADMIN_PASSWORD: 'pw',
    ACCESS_TOKEN_TTL_SECONDS: '3600',
    REFRESH_TOKEN_TTL_SECONDS: '2592000',
  };
}

async function jsonRequest(app, env, pathname, { method, payload, token } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
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

test('auth register refresh and change password flow works', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const registered = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw1', displayName: 'Alice' },
  });
  assert.equal(registered.status, 200);
  assert.equal(registered.json.user.username, 'alice');

  const duplicate = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw1', displayName: 'Alice 2' },
  });
  assert.equal(duplicate.status, 409);

  const loginFail = await jsonRequest(app, env, '/v1/auth/login', {
    method: 'POST',
    payload: { username: 'alice', password: 'bad' },
  });
  assert.equal(loginFail.status, 401);

  const login = await jsonRequest(app, env, '/v1/auth/login', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw1' },
  });
  assert.equal(login.status, 200);
  const accessToken = String(login.json.accessToken);
  const refreshToken = String(login.json.refreshToken);

  const me = await jsonRequest(app, env, '/v1/me', { token: accessToken });
  assert.equal(me.status, 200);
  assert.equal(me.json.displayName, 'Alice');

  const refreshed = await jsonRequest(app, env, '/v1/auth/refresh', {
    method: 'POST',
    payload: { refreshToken },
  });
  assert.equal(refreshed.status, 200);

  const passwordChanged = await jsonRequest(app, env, '/v1/me/password', {
    method: 'POST',
    token: accessToken,
    payload: { currentPassword: 'pw1', newPassword: 'pw2' },
  });
  assert.equal(passwordChanged.status, 200);

  const oldLogin = await jsonRequest(app, env, '/v1/auth/login', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw1' },
  });
  assert.equal(oldLogin.status, 401);

  const newLogin = await jsonRequest(app, env, '/v1/auth/login', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw2' },
  });
  assert.equal(newLogin.status, 200);
});

test('variant asset lifecycle includes history, search, subscribe, fork, and conflicts', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw', displayName: 'Alice' },
  });
  const bob = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'bob', password: 'pw', displayName: 'Bob' },
  });
  const aliceToken = String(alice.json.accessToken);
  const bobToken = String(bob.json.accessToken);

  const created = await jsonRequest(app, env, '/v1/variants', {
    method: 'POST',
    token: aliceToken,
    payload: variantPayload({ variantId: 'alice-variant', name: 'Alice Private', visibility: 'private' }),
  });
  assert.equal(created.status, 200);
  assert.equal(created.json.assetType, 'variant');
  assert.equal(created.json.revision, 'r1');
  assert.equal(created.json.editable, true);

  const publicSearchBefore = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=public');
  assert.equal(publicSearchBefore.status, 200);
  assert.equal(publicSearchBefore.json.entries.length, 0);

  const privateByBob = await jsonRequest(app, env, '/v1/variants/alice-variant', { token: bobToken });
  assert.equal(privateByBob.status, 403);

  const updated = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    token: aliceToken,
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
    token: bobToken,
  });
  assert.equal(subscribed.status, 200);
  assert.equal(subscribed.json.subscribed, true);
  assert.equal(subscribed.json.editable, false);

  const subscribedSearch = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=subscribed', {
    token: bobToken,
  });
  assert.equal(subscribedSearch.status, 200);
  assert.equal(subscribedSearch.json.entries.length, 1);
  assert.equal(subscribedSearch.json.entries[0].assetId, 'alice-variant');

  const forbiddenEdit = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    token: bobToken,
    payload: variantPayload({
      variantId: 'alice-variant',
      name: 'Bob Edit',
      visibility: 'public',
      revision: updated.json.revision,
    }),
  });
  assert.equal(forbiddenEdit.status, 403);

  const history = await jsonRequest(app, env, '/v1/variants/alice-variant/versions', { token: aliceToken });
  assert.equal(history.status, 200);
  assert.equal(history.json.versions.length, 2);
  assert.equal(history.json.versions[0].versionNumber, 2);

  const oldVersion = await jsonRequest(app, env, '/v1/variants/alice-variant/versions/1', { token: aliceToken });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.record.name, 'Alice Private');

  const conflict = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'PUT',
    token: aliceToken,
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
    token: bobToken,
    payload: { variantId: 'bob-fork', name: 'Bob Fork' },
  });
  assert.equal(forked.status, 200);
  assert.equal(forked.json.assetId, 'bob-fork');
  assert.equal(forked.json.visibility, 'private');
  assert.equal(forked.json.editable, true);

  const bobMine = await jsonRequest(app, env, '/v1/variants?owner=me', { token: bobToken });
  assert.equal(bobMine.status, 200);
  assert.equal(bobMine.json.entries.length, 1);
  assert.equal(bobMine.json.entries[0].assetId, 'bob-fork');

  const unsubscribed = await jsonRequest(app, env, '/v1/variants/alice-variant/subscribe', {
    method: 'DELETE',
    token: bobToken,
  });
  assert.equal(unsubscribed.status, 200);

  const removed = await jsonRequest(app, env, '/v1/variants/alice-variant', {
    method: 'DELETE',
    token: aliceToken,
  });
  assert.equal(removed.status, 200);

  const publicAfterDelete = await jsonRequest(app, env, '/v1/search?assetType=variant&owner=public');
  assert.equal(publicAfterDelete.status, 200);
  assert.equal(publicAfterDelete.json.entries.length, 0);
});

test('component asset lifecycle validates published session envelope and visibility rules', async (t) => {
  const env = createEnv();
  t.after(() => env.DB.close());
  const app = createApp();

  const alice = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'alice', password: 'pw', displayName: 'Alice' },
  });
  const bob = await jsonRequest(app, env, '/v1/auth/register', {
    method: 'POST',
    payload: { username: 'bob', password: 'pw', displayName: 'Bob' },
  });
  const aliceToken = String(alice.json.accessToken);
  const bobToken = String(bob.json.accessToken);

  const invalid = await jsonRequest(app, env, '/v1/components', {
    method: 'POST',
    token: aliceToken,
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
    token: aliceToken,
    payload: componentPayload({ componentId: 'component-a', name: 'Published Session', visibility: 'public' }),
  });
  assert.equal(created.status, 200);
  assert.equal(created.json.record.schemaVersion, 'f8studio-session/1');

  const publicList = await jsonRequest(app, env, '/v1/components?owner=public');
  assert.equal(publicList.status, 200);
  assert.equal(publicList.json.entries.length, 1);

  const subscribed = await jsonRequest(app, env, '/v1/components/component-a/subscribe', {
    method: 'POST',
    token: bobToken,
  });
  assert.equal(subscribed.status, 200);
  assert.equal(subscribed.json.subscribed, true);
  assert.equal(subscribed.json.editable, false);

  const history1 = await jsonRequest(app, env, '/v1/components/component-a/versions', { token: aliceToken });
  assert.equal(history1.status, 200);
  assert.equal(history1.json.versions.length, 1);

  const updated = await jsonRequest(app, env, '/v1/components/component-a', {
    method: 'PUT',
    token: aliceToken,
    payload: componentPayload({
      componentId: 'component-a',
      name: 'Published Session v2',
      visibility: 'public',
      revision: created.json.revision,
    }),
  });
  assert.equal(updated.status, 200);
  assert.equal(updated.json.revision, 'r2');

  const oldVersion = await jsonRequest(app, env, '/v1/components/component-a/versions/1', { token: bobToken });
  assert.equal(oldVersion.status, 200);
  assert.equal(oldVersion.json.record.name, 'Published Session');

  const forbidden = await jsonRequest(app, env, '/v1/components/component-a', {
    method: 'PUT',
    token: bobToken,
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
    token: bobToken,
    payload: { componentId: 'component-b', name: 'Bob Session Copy' },
  });
  assert.equal(forked.status, 200);
  assert.equal(forked.json.assetId, 'component-b');
  assert.equal(forked.json.visibility, 'private');
});
