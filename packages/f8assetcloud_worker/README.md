# f8assetcloud_worker

Cloudflare Worker + D1 backend for Feel8 asset management, rebuilt around `Hono + Better Auth`.

## Architecture

- Auth is provided by Better Auth at `/api/auth/*`
- Business APIs stay under `/v1/*`
- The long-term public API contract should be owned under `/v1/*`, not delegated to Better Auth internals
- OpenAPI docs for audited `/v1/*` endpoints are served at `/docs` and `/openapi.json`
- User and management console is the React app in `console_web`
- The web UI is served at `/console`, and `/` redirects to `/console/`
- Authentication uses Better Auth cookie sessions, not custom JWTs
- D1 schema starts from a single fresh migration: `migrations/0001_init.sql`

## Database model

Core auth tables:

- `user`
- `session`
- `account`
- `verification`

Asset tables:

- `asset_heads`
- `variant_details`
- `asset_versions`
- `asset_subscriptions`

Asset storage contract:

- `asset_heads` stores current queryable metadata for all assets:
  - owner
  - visibility
  - current revision and version number
  - name
  - description
  - tags
  - component `schema_version`
- `variant_details` stores current variant-specific metadata:
  - `variant_kind`
  - `base_node_type`
  - `service_class`
  - `operator_class`
- `asset_versions` stores versioned large payload blobs only:
  - component versions store canonical session content `{ schemaVersion, layout }`
  - variant versions store canonical `spec`
- `GET .../content` reconstructs a full API `record` from current relational metadata plus the versioned blob payload
- Historical content is versioned; historical metadata is not. `/content` always returns the current canonical API `record`, reconstructed from the current head metadata plus the selected version blob.

## Auth features

- Username + password sign-in
- Email verification
- Password reset
- Google OAuth login
- Management permissions are backed by Better Auth role support
- Bootstrap management account creation from environment variables

## Main routes

Auth:

- `POST /api/auth/sign-up/email`
- `POST /api/auth/sign-in/username`
- `POST /api/auth/sign-out`
- `POST /api/auth/request-password-reset`
- `GET /api/auth/get-session`
- `GET /api/auth/callback/google`

App wrappers:

- `GET /v1/auth/providers`
- `GET /v1/auth/verify-email?token=...`
- `POST /v1/auth/reset-password`
- `GET /v1/me`
- `POST /v1/me/password`

Web UI:

- `GET /console`
- `GET /console/verify-email`
- `GET /console/reset-password`
- `GET /` redirects to `/console/`

OpenAPI:

- `GET /docs`
- `GET /openapi.json`

Assets:

- `GET /v1/variants`
- `POST /v1/variants`
- `GET /v1/variants/:variantId`
- `PUT /v1/variants/:variantId`
- `DELETE /v1/variants/:variantId`
- `GET /v1/variants/:variantId/versions`
- `GET /v1/variants/:variantId/versions/:versionNumber`
- `POST /v1/variants/:variantId/subscribe`
- `DELETE /v1/variants/:variantId/subscribe`
- `POST /v1/variants/:variantId/fork`
- `GET /v1/components`
- `POST /v1/components`
- `GET /v1/components/:componentId`
- `PUT /v1/components/:componentId`
- `DELETE /v1/components/:componentId`
- `GET /v1/components/:componentId/versions`
- `GET /v1/components/:componentId/versions/:versionNumber`
- `POST /v1/components/:componentId/subscribe`
- `DELETE /v1/components/:componentId/subscribe`
- `POST /v1/components/:componentId/fork`
- `GET /v1/components/:componentId/content`
- `GET /v1/components/:componentId/versions/:versionNumber/content`
- `GET /v1/variants/:variantId/content`
- `GET /v1/variants/:variantId/versions/:versionNumber/content`

Management:

- `GET /v1/management/users`
- `POST /v1/management/users`
- `GET /v1/management/users/:userId`
- `PUT /v1/management/users/:userId`
- `DELETE /v1/management/users/:userId`
- `GET /v1/management/site-settings`
- `PUT /v1/management/site-settings`
- `GET /v1/management/components`
- `GET /v1/management/components/:componentId`
- `PUT /v1/management/components/:componentId`
- `DELETE /v1/management/components/:componentId`
- `GET /v1/management/variants`
- `GET /v1/management/variants/:variantId`
- `PUT /v1/management/variants/:variantId`
- `DELETE /v1/management/variants/:variantId`

OpenAPI contract:

- `/openapi.json` and `/docs` should be treated as the canonical audited API contract for `/v1/*`
- When routes or payloads change, update `src/openapi.js` in the same change so the runtime docs stay in sync

## Environment

Required:

- `BETTER_AUTH_SECRET`
- `BOOTSTRAP_ADMIN_USERNAME`
- `BOOTSTRAP_ADMIN_PASSWORD`

Recommended:

- `BOOTSTRAP_ADMIN_DISPLAY_NAME`
- `BOOTSTRAP_ADMIN_EMAIL`
- `AUTH_BASE_URL`
- `AUTH_VERIFY_EMAIL_BASE_URL`
- `AUTH_RESET_PASSWORD_BASE_URL`
- `EMAIL_VERIFY_TOKEN_TTL_SECONDS`
- `PASSWORD_RESET_TOKEN_TTL_SECONDS`
- `ENABLE_ASSET_JSON_GZIP`
- `ENABLE_API_JSON_GZIP`

CORS:

- `CORS_ALLOWED_ORIGINS` — comma-separated list of extra allowed origins (e.g. `http://localhost:5173`)

Google login:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Email delivery via Resend:

- `AUTH_EMAIL_FROM`
- `RESEND_API_KEY`

Local debug only:

- `EXPOSE_DEBUG_AUTH_LINKS=true` — prints verification/reset links to console (only effective when email delivery is not configured)
- `ENABLE_ASSET_JSON_GZIP=true` — targeted default; compresses large asset content responses such as `/v1/components/:componentId/content` without touching auth routes
- `ENABLE_API_JSON_GZIP=false` — broad `/v1/*` compression; keep this off unless you intentionally want to gzip nearly every app JSON response

Variable precedence:

- `wrangler.toml` `[vars]` provides checked-in defaults for deploys and local dev.
- `.dev.vars` overrides those values during `wrangler dev`, so local debugging should usually be adjusted there.

## Local development

```bash
cd packages/f8assetcloud_worker
cp .dev.vars.example .dev.vars
npm install
npm --prefix console_web install
npm run web:dev
npm run d1:migrate:local
npx wrangler dev
```

Single-origin mode:

```bash
cd packages/f8assetcloud_worker
npm run dev:single
```

## Deployment

```bash
cd packages/f8assetcloud_worker
npm run web:build
npm run d1:migrate
npx wrangler deploy
```

## Notes

- This package now assumes a brand-new database baseline.
- Old JWT auth tables and compatibility migrations have been removed.
- To fully reset locally or in a disposable environment, recreate the D1 database and apply `0001_init.sql`.
- A cron trigger runs daily at 03:00 UTC to clean up expired sessions.
- Asset version `content` is stored as a GZIP-compressed BLOB in D1.
- HTTP compression is negotiated only at the transport layer with standard `Content-Encoding` / `Accept-Encoding`.
- The worker does not use field-level compression contracts. The canonical stored payload itself is already the minimal versioned blob:
  - component: `{ schemaVersion, layout }`
  - variant: `spec`
- Limit: 10 MB per version before storage compression.
- New application-owned endpoints should be added to the OpenAPI contract in `src/openapi.js` as part of the route change, so docs and clients do not drift from implementation.
