import { createApp } from './app.js';

const app = createApp();

export default {
  async fetch(request, env, ctx) {
    return app.fetch(request, env, ctx);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(cleanupExpiredSessions(env));
  },
};

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
