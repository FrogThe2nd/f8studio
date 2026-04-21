import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiFetch } from './api.js';

describe('apiFetch', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('surfaces backend error messages in ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ message: 'Private asset not found' }), {
        status: 404,
        headers: {
          'Content-Type': 'application/json',
        },
      })),
    );

    await expect(apiFetch('/v1/assets/private-1')).rejects.toEqual(
      expect.objectContaining({
        name: 'ApiError',
        status: 404,
        message: 'Private asset not found',
      }),
    );
  });

  it('falls back to a default message when the backend omits one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{}', {
        status: 500,
        headers: {
          'Content-Type': 'application/json',
        },
      })),
    );

    await expect(apiFetch('/v1/site-settings')).rejects.toEqual(
      expect.objectContaining({
        name: 'ApiError',
        status: 500,
        message: 'Request failed (500)',
      }),
    );
  });
});
