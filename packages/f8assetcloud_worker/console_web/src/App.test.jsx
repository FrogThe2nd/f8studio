import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUseSession, mockRefetch, mockSignOut } = vi.hoisted(() => ({
  mockUseSession: vi.fn(),
  mockRefetch: vi.fn(),
  mockSignOut: vi.fn(),
}));

vi.mock('./authClient.js', () => ({
  authClient: {
    useSession: () => mockUseSession(),
    signOut: mockSignOut,
    signIn: {
      username: vi.fn(),
      social: vi.fn(),
    },
    signUp: {
      email: vi.fn(),
    },
    requestPasswordReset: vi.fn(),
    changePassword: vi.fn(),
  },
}));

import { ConsoleRootApp } from './App.jsx';

function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  });
}

describe('ConsoleRootApp session recovery', () => {
  beforeEach(() => {
    mockRefetch.mockReset();
    mockRefetch.mockResolvedValue(undefined);
    mockSignOut.mockReset();
    mockSignOut.mockResolvedValue(undefined);
    mockUseSession.mockReset();
    mockUseSession.mockReturnValue({
      data: {
        user: {
          id: 'user-1',
        },
      },
      isPending: false,
      refetch: mockRefetch,
    });
    window.history.replaceState({}, '', '/console/');
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('shows recovery actions when a remembered session never hydrates a profile', async () => {
    vi.useFakeTimers();
    const stalledRequest = new Promise(() => {});
    const fetchMock = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : String(input.url);
      if (url === '/v1/auth/providers') {
        return jsonResponse({ google: false });
      }
      if (url === '/v1/site-settings') {
        return jsonResponse({ allowUserRegistration: false });
      }
      if (url === '/v1/me' || url === '/api/auth/list-accounts') {
        return stalledRequest;
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ConsoleRootApp />);

    expect(screen.getByText('Loading session...')).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    expect(screen.getByText('Your session was found, but loading your profile is taking longer than expected.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry Profile Load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign Out' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reload Page' })).toBeTruthy();
  });

  it('renders the console when the remembered session loads the profile successfully', async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : String(input.url);
      if (url === '/v1/auth/providers') {
        return jsonResponse({ google: false });
      }
      if (url === '/v1/site-settings') {
        return jsonResponse({ allowUserRegistration: false });
      }
      if (url === '/v1/me') {
        return jsonResponse({
          userId: 'user-1',
          username: 'alice',
          displayName: 'Alice',
          email: 'alice@example.com',
          emailVerified: true,
          role: 'user',
          isAdmin: false,
        });
      }
      if (url === '/api/auth/list-accounts') {
        return jsonResponse([{ providerId: 'credential', accountId: 'alice' }]);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ConsoleRootApp />);

    expect(await screen.findByText('Welcome, Alice')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Logout' })).toBeTruthy();
    expect(screen.getByText(/alice@example\.com/i)).toBeTruthy();
  });

  it('hides Google entry points on the login screen when public registration is disabled', async () => {
    mockUseSession.mockReturnValue({
      data: null,
      isPending: false,
      refetch: mockRefetch,
    });

    const fetchMock = vi.fn(async (input) => {
      const url = typeof input === 'string' ? input : String(input.url);
      if (url === '/v1/auth/providers') {
        return jsonResponse({ google: true });
      }
      if (url === '/v1/site-settings') {
        return jsonResponse({ allowUserRegistration: false });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ConsoleRootApp />);

    expect(await screen.findByText('Sign in to continue.')).toBeTruthy();
    expect(screen.queryByText('Google sign-in is available for direct login and for linking to an existing account.')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Continue with Google' })).toBeNull();
    expect(screen.getByText('New account registration is currently disabled.')).toBeTruthy();
  });
});
