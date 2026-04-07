import { createApp } from './app.js';

const app = createApp();

export default {
  async fetch(request, env, ctx) {
    const response = await app.fetch(request, env, ctx);
    return maybeCompressResponse(request, response, env);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(cleanupExpiredSessions(env));
  },
};

function maybeCompressResponse(request, response, env) {
  if (!shouldCompressResponse(request, response, env) || response.body === null) {
    return response;
  }
  const headers = new Headers(response.headers);
  headers.set('Content-Encoding', 'gzip');
  headers.set('Cache-Control', appendCacheControlDirective(headers.get('Cache-Control'), 'no-transform'));
  headers.set('Vary', appendVaryValue(headers.get('Vary'), 'Accept-Encoding'));
  headers.delete('Content-Length');
  const compressedBody = response.body.pipeThrough(new CompressionStream('gzip'));
  return new Response(compressedBody, {
    status: response.status,
    statusText: response.statusText,
    headers,
    encodeBody: 'manual',
  });
}

function shouldCompressResponse(request, response, env) {
  if (request.method === 'HEAD') {
    return false;
  }
  const url = new URL(request.url);
  if (!shouldCompressPath(request, url, env)) {
    return false;
  }
  const acceptEncoding = String(request.headers.get('Accept-Encoding') || '').toLowerCase();
  if (!acceptEncoding.includes('gzip')) {
    return false;
  }
  if (!response.ok) {
    return false;
  }
  if (response.headers.has('Content-Encoding')) {
    return false;
  }
  if (response.headers.has('Set-Cookie')) {
    return false;
  }
  const contentType = String(response.headers.get('Content-Type') || '').toLowerCase();
  if (!contentType.includes('application/json')) {
    return false;
  }
  return true;
}

function shouldCompressPath(request, url, env) {
  if (!url.pathname.startsWith('/v1/')) {
    return false;
  }
  if (url.pathname.startsWith('/v1/auth/')) {
    return false;
  }
  if (isApiJsonCompressionEnabled(env)) {
    return true;
  }
  if (!isAssetJsonCompressionEnabled(env)) {
    return false;
  }
  return isLargeAssetPayloadRoute(request, url.pathname);
}

function isApiJsonCompressionEnabled(env) {
  return String(env?.ENABLE_API_JSON_GZIP || '').trim().toLowerCase() === 'true';
}

function isAssetJsonCompressionEnabled(env) {
  const configured = String(env?.ENABLE_ASSET_JSON_GZIP || '').trim().toLowerCase();
  if (!configured) {
    return true;
  }
  return configured !== 'false' && configured !== '0' && configured !== 'no';
}

function isLargeAssetPayloadRoute(request, pathname) {
  if (!/^\/v1\/(variants|components)\//.test(pathname)) {
    return false;
  }
  if (request.method !== 'GET') {
    return false;
  }
  return /\/content$/.test(pathname);
}

function appendVaryValue(currentValue, nextValue) {
  const existing = String(currentValue || '').trim();
  if (!existing) {
    return nextValue;
  }
  const parts = existing.split(',').map((part) => part.trim().toLowerCase());
  if (parts.includes(String(nextValue).toLowerCase())) {
    return existing;
  }
  return `${existing}, ${nextValue}`;
}

function appendCacheControlDirective(currentValue, nextValue) {
  const existing = String(currentValue || '').trim();
  if (!existing) {
    return nextValue;
  }
  const parts = existing.split(',').map((part) => part.trim().toLowerCase());
  if (parts.includes(String(nextValue).toLowerCase())) {
    return existing;
  }
  return `${existing}, ${nextValue}`;
}

async function cleanupExpiredSessions(env) {
  const db = env?.DB;
  if (!db) {
    return;
  }
  const result = await db.prepare(
    'DELETE FROM session WHERE expiresAt < ?',
  )
    .bind(Date.now())
    .run();
  const deleted = Number(result?.meta?.changes || 0);
  if (deleted > 0) {
    console.info(`[cron] cleaned up ${deleted} expired session(s)`);
  }
}
