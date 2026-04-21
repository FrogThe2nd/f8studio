# Asset Cloud Desktop Browser Sign-In

This note documents the desktop browser sign-in flow used by PyStudio when signing in to Asset Cloud with the **system browser + local loopback callback + authorization code + token exchange** pattern.

It also records the environment variables that matter when switching between **production**, **preview**, and **local development** environments.

## Why this flow exists

PyStudio does not embed the full website login UI inside a desktop dialog.

Instead, it opens the system browser and lets the user sign in on the Asset Cloud website. The desktop app then receives an authorization code over a temporary local HTTP callback and exchanges that code for the authenticated browser session.

This gives us:

- first-class website login UX
- support for Google sign-in and future social providers
- no password handling inside a custom Qt webview
- environment-specific website flows while keeping the desktop app simple

## End-to-end flow

### 1. PyStudio creates a desktop browser auth session

PyStudio builds a PKCE session with:

- `client_id = pystudio`
- a random `state`
- a PKCE `code_verifier` / `code_challenge`
- a temporary loopback callback such as `http://127.0.0.1:43001/callback`

Relevant code:

- `packages/f8pystudio/f8pystudio/assets/common/browser_auth.py`

### 2. PyStudio opens the system browser

The browser is sent to:

- `GET /v1/auth/desktop/authorize?...`

on the currently configured Asset Cloud base URL.

That base URL can be production, preview, or local dev depending on configuration.

### 3. The website handles sign-in

The worker renders the desktop auth page and can:

- sign in with email/password
- continue directly if the browser already has an authenticated session
- start Google sign-in when Google auth is configured

Relevant code:

- `packages/f8assetcloud_worker/src/app.js`
- `packages/f8assetcloud_worker/test/app.test.js`

### 4. The worker redirects back to the loopback callback with an authorization code

After successful website authentication, the worker stores a short-lived desktop authorization code in D1 and redirects the browser to the loopback callback.

The `redirect_uri` is intentionally restricted to loopback HTTP addresses:

- `http://127.0.0.1:...`
- `http://localhost:...`
- `http://[::1]:...`

That constraint is enforced by `requireLoopbackRedirectUri(...)` in the worker.

### 5. PyStudio receives the callback locally

PyStudio runs a temporary local HTTP server for the callback. It reads the `code` and `state` from the browser request and validates the `state` before continuing.

### 6. PyStudio exchanges the authorization code for the browser session

PyStudio posts to:

- `POST /v1/auth/desktop/token`

with:

- `clientId`
- `code`
- `redirectUri`
- `codeVerifier`

The worker validates:

- the code exists and has not expired
- the code was not already used
- the `clientId` matches
- the `redirectUri` matches
- the PKCE verifier matches the stored challenge

If everything is valid, the worker returns:

- `sessionCookie`
- `user`

PyStudio then stores the resulting session against the current account identity.

## Current browser UX details

The local callback still exists, but we now minimize how visible it is:

- the loopback callback page first tries `window.close()`
- if the browser does not allow closing the tab, it falls back to the website
- success fallback goes to `/console/auth-complete`
- error fallback goes to `/console/auth-error`

The website now renders friendly pages for both routes.

### Success path UX

1. local callback receives `code`
2. browser tries to close itself
3. fallback opens `${base_url}/console/auth-complete`
4. the completion page waits briefly, then redirects to `${base_url}/console/`

### Error path UX

1. local callback receives an error payload
2. browser falls back to `${base_url}/console/auth-error`
3. the page stays in place so the user can read the failure details

## Environment variables

There are two sides to configure: **PyStudio** and the **Asset Cloud worker**.

### PyStudio

#### `F8_ASSET_CLOUD_BASE_URL`

This selects which Asset Cloud deployment PyStudio should talk to.

Examples:

```bash
# production
export F8_ASSET_CLOUD_BASE_URL=https://assetcloud.feel8.fun

# preview
export F8_ASSET_CLOUD_BASE_URL=https://preview-assetcloud.feel8.fun

# local worker
export F8_ASSET_CLOUD_BASE_URL=http://127.0.0.1:8787
```

PyStudio base URL precedence is:

1. `F8_ASSET_CLOUD_BASE_URL`
2. saved `QSettings` value (`assetcloud/v1/base_url`)
3. the built-in default (`https://assetcloud.feel8.fun`)

Important consequence:

- exporting `F8_ASSET_CLOUD_BASE_URL` now **does** override any previously persisted `QSettings` base URL
- this is the recommended way to force PyStudio onto production, preview, or a local worker during development
- when this override points at a different environment, PyStudio only treats saved sessions for that same base URL as active/switchable

Relevant code:

- `packages/f8pystudio/f8pystudio/assets/common/common.py`
- `packages/f8pystudio/f8pystudio/assets/components/component_sync.py`
- `packages/f8pystudio/f8pystudio/assets/variants/variant_sync.py`

### Asset Cloud worker

#### `AUTH_BASE_URL`

This is the canonical base URL Better Auth should use for callback and auth URL generation.

Examples:

```bash
AUTH_BASE_URL="https://assetcloud.feel8.fun"
AUTH_BASE_URL="https://preview-assetcloud.feel8.fun"
AUTH_BASE_URL="http://localhost:8787"
```

If `AUTH_BASE_URL` is not set, the worker falls back to the origin of the current request.

#### `CORS_ALLOWED_ORIGINS`

Comma-separated extra allowed origins. Use this when the website and development clients are served from different origins.

Examples:

```bash
CORS_ALLOWED_ORIGINS="http://localhost:5173"
CORS_ALLOWED_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
```

#### Related auth delivery settings

These are not specific to desktop sign-in, but usually matter in the same environments:

- `AUTH_VERIFY_EMAIL_BASE_URL`
- `AUTH_RESET_PASSWORD_BASE_URL`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Relevant code:

- `packages/f8assetcloud_worker/src/app.js`
- `packages/f8assetcloud_worker/README.md`
- `packages/f8assetcloud_worker/.dev.vars.example`

## Recommended environment setups

### Production

PyStudio:

```bash
export F8_ASSET_CLOUD_BASE_URL=https://assetcloud.feel8.fun
```

Worker:

```bash
AUTH_BASE_URL="https://assetcloud.feel8.fun"
```

### Preview

PyStudio:

```bash
export F8_ASSET_CLOUD_BASE_URL=https://preview-assetcloud.feel8.fun
```

Worker:

```bash
AUTH_BASE_URL="https://preview-assetcloud.feel8.fun"
```

### Local worker development

PyStudio:

```bash
export F8_ASSET_CLOUD_BASE_URL=http://127.0.0.1:8787
```

Worker:

```bash
AUTH_BASE_URL="http://localhost:8787"
CORS_ALLOWED_ORIGINS="http://localhost:5173"
```

## Troubleshooting notes

### PyStudio still opens the wrong site after setting `F8_ASSET_CLOUD_BASE_URL`

Most likely cause:

- PyStudio was launched from a shell or launcher that does not actually include the environment variable

Fix:

- verify the process environment really contains `F8_ASSET_CLOUD_BASE_URL`
- restart PyStudio from the same shell/session where you exported it

### The browser reaches the website but desktop sign-in does not finish

Check:

- the loopback callback port is reachable locally
- the worker still accepts the exact `redirect_uri`
- the `state` returned from the browser matches the one PyStudio created
- the auth code has not expired or already been used

### Browser closes or redirects but PyStudio still asks to sign in again

Check:

- whether the worker returned a rotated session cookie
- whether PyStudio persisted the rotated cookie for the correct account
- whether the effective PyStudio base URL matches the site you actually used to sign in

Recent client changes already:

- persist rotated cookies back into saved account storage
- clear invalid saved sessions after a real `401`
- avoid repeatedly retrying expired sessions

## Files touched by this feature area

Worker:

- `packages/f8assetcloud_worker/src/app.js`
- `packages/f8assetcloud_worker/test/app.test.js`
- `packages/f8assetcloud_worker/migrations/0002_desktop_auth_codes.sql`
- `packages/f8assetcloud_worker/console_web/src/App.jsx`
- `packages/f8assetcloud_worker/console_web/src/App.test.jsx`

PyStudio:

- `packages/f8pystudio/f8pystudio/assets/common/browser_auth.py`
- `packages/f8pystudio/f8pystudio/assets/ui/asset_cloud_account_menu.py`
- `packages/f8pystudio/f8pystudio/assets/components/component_sync.py`
- `packages/f8pystudio/f8pystudio/assets/variants/variant_sync.py`
- `packages/f8pystudio/tests/test_asset_cloud_browser_auth.py`
- `packages/f8pystudio/tests/test_asset_cloud_account_menu.py`
- `packages/f8pystudio/tests/test_component_sync.py`
- `packages/f8pystudio/tests/test_variant_sync.py`
