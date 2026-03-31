# f8assetcloud_worker

Cloudflare Worker + D1 backend for the unified Feel8 asset catalog: users, variants, components, subscriptions, and immutable version history.

## Why this deployment shape

- No VPS is required.
- Worker execution, HTTPS, and edge routing are managed by Cloudflare.
- D1 gives us a managed SQLite-compatible store with migrations.
- Secrets stay outside the repo via Wrangler / Cloudflare secrets.

Cloudflare docs:

- D1 bindings and setup: https://developers.cloudflare.com/d1/get-started/
- D1 migrations: https://developers.cloudflare.com/d1/reference/migrations/
- Worker secrets and `.dev.vars`: https://developers.cloudflare.com/workers/configuration/secrets/

## Open-source safe config

Committed files stay secret-free:

- `wrangler.toml` contains placeholders and non-secret defaults only.
- `.dev.vars.example` documents required secrets without real values.
- Real `JWT_SECRET`, bootstrap password, and local `.dev.vars` never belong in git.

Required secrets:

```bash
cd packages/f8assetcloud_worker
npx wrangler secret put JWT_SECRET
npx wrangler secret put BOOTSTRAP_ADMIN_PASSWORD
```

## Data model

D1 schema uses five tables:

- `users`
- `refresh_tokens`
- `asset_heads`
- `asset_versions`
- `asset_subscriptions`

Storage rule:

- searchable metadata lives in first-class columns
- full variant/component content lives in `content_json TEXT`
- history is append-only in `asset_versions`
- no JSON blobs are stored as BLOBs

## Asset model

- `variant` content keeps the existing `record` payload shape used by Studio
- `component` content stores a published session envelope with `schemaVersion = f8studio-session/1`
- `private/public` visibility applies to both asset types
- subscriptions are read-only links; editing requires `fork`
- updates create a new immutable version and increment `revision`

## API surface

Auth:

- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `GET /v1/me`
- `POST /v1/me/password`

Variants:

- `GET /v1/variants`
- `GET /v1/variants/:variantId`
- `POST /v1/variants`
- `PUT /v1/variants/:variantId`
- `DELETE /v1/variants/:variantId`
- `GET /v1/variants/:variantId/versions`
- `GET /v1/variants/:variantId/versions/:versionNumber`
- `POST /v1/variants/:variantId/subscribe`
- `DELETE /v1/variants/:variantId/subscribe`
- `POST /v1/variants/:variantId/fork`

Components:

- `GET /v1/components`
- `GET /v1/components/:componentId`
- `POST /v1/components`
- `PUT /v1/components/:componentId`
- `DELETE /v1/components/:componentId`
- `GET /v1/components/:componentId/versions`
- `GET /v1/components/:componentId/versions/:versionNumber`
- `POST /v1/components/:componentId/subscribe`
- `DELETE /v1/components/:componentId/subscribe`
- `POST /v1/components/:componentId/fork`

Search:

- `GET /v1/search?assetType=variant|component&q=&visibility=&owner=me|subscribed|public&cursor=`

## Local development

```bash
cd packages/f8assetcloud_worker
cp .dev.vars.example .dev.vars
npm install
npm test
npx wrangler dev
```

## D1 setup

If you already created the database in Cloudflare, copy its IDs into `wrangler.toml`.

```bash
cd packages/f8assetcloud_worker
npx wrangler d1 create feel8-assets
npx wrangler d1 migrations apply feel8-assets --local
npx wrangler d1 migrations apply feel8-assets
```

## Notes

- unauthenticated search only sees public assets
- authenticated search sees own private assets plus all public assets
- historical versions are read-only; rollback should be implemented as a new save from old content
- password change revokes existing refresh tokens
