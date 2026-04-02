# f8assetcloud_worker

Cloudflare Worker + D1 backend for Feel8 asset management, rebuilt around `Hono + Better Auth`.

## Architecture

- Auth is provided by Better Auth at `/api/auth/*`
- Business APIs stay under `/v1/*`
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
- `asset_versions`
- `asset_subscriptions`

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

Assets:

- `GET /v1/search`
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

Management:

- `GET /v1/management/users`
- `POST /v1/management/users`
- `GET /v1/management/users/:userId`
- `PUT /v1/management/users/:userId`
- `DELETE /v1/management/users/:userId`
- `GET /v1/management/users/:userId/assets`
- `GET /v1/management/assets`
- `GET /v1/management/assets/:assetId`
- `PUT /v1/management/assets/:assetId`
- `DELETE /v1/management/assets/:assetId`

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

Google login:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Email delivery via Resend:

- `AUTH_EMAIL_FROM`
- `RESEND_API_KEY`

Local debug only:

- `EXPOSE_DEBUG_AUTH_LINKS=true`

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
