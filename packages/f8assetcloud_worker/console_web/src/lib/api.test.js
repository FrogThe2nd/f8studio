import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiFetch, registerWithPassword, requestPasswordResetEmail } from './api.js';

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

  it('attaches the captcha header for password registration', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await registerWithPassword(
      {
        name: 'Alice',
        email: 'alice@example.com',
        password: 'password123',
        callbackURL: 'http://localhost:3000/verify-email?verified=1',
      },
      { captchaResponse: 'captcha-token-1' },
    );

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/sign-up/email', expect.objectContaining({
      headers: expect.objectContaining({
        'Content-Type': 'application/json',
        'X-Captcha-Response': 'captcha-token-1',
      }),
    }));
  });

  it('attaches the captcha header for password reset requests', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
      },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await requestPasswordResetEmail(
      {
        email: 'alice@example.com',
        redirectTo: 'http://localhost:3000/reset-password',
      },
      { captchaResponse: 'captcha-token-2' },
    );

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/request-password-reset', expect.objectContaining({
      headers: expect.objectContaining({
        'Content-Type': 'application/json',
        'X-Captcha-Response': 'captcha-token-2',
      }),
    }));
  });
});
