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

import {
  buildAssetListPath,
  buildManagedAssetDetailPath,
  buildManagedAssetListPath,
  ConsoleRootApp,
  downloadableContentForAsset,
} from './App.jsx';

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

describe('downloadableContentForAsset', () => {
  it('extracts canonical component content from the content endpoint response', () => {
    const result = downloadableContentForAsset(
      { assetType: 'component', assetId: 'component-1' },
      {
        componentId: 'component-1',
        assetType: 'component',
        versionNumber: 3,
        revision: 'r3',
        record: {
          componentId: 'component-1',
          name: 'Component One',
          description: '',
          tags: [],
          schemaVersion: 'f8studio-session/1',
          content: {
            schemaVersion: 'f8studio-session/1',
            layout: {
              nodes: {},
              connections: [],
            },
          },
          createdAt: '2026-04-06T00:00:00Z',
          updatedAt: '2026-04-06T00:00:00Z',
        },
      },
    );

    expect(result).toEqual({
      filename: 'component-component-1-content.json',
      data: {
        schemaVersion: 'f8studio-session/1',
        layout: {
          nodes: {},
          connections: [],
        },
      },
    });
  });

  it('extracts canonical variant spec from the content endpoint response', () => {
    const result = downloadableContentForAsset(
      { assetType: 'variant', assetId: 'variant-1' },
      {
        variantId: 'variant-1',
        assetType: 'variant',
        versionNumber: 2,
        revision: 'r2',
        record: {
          variantId: 'variant-1',
          kind: 'operator',
          baseNodeType: 'svc.a.op',
          serviceClass: 'svc.a',
          operatorClass: 'svc.a.op',
          name: 'Variant One',
          description: '',
          tags: [],
          spec: {
            label: 'Variant One',
          },
          createdAt: '2026-04-06T00:00:00Z',
          updatedAt: '2026-04-06T00:00:00Z',
        },
      },
    );

    expect(result).toEqual({
      filename: 'variant-variant-1-spec.json',
      data: {
        label: 'Variant One',
      },
    });
  });
});

describe('management asset paths', () => {
  it('builds typed public asset list paths', () => {
    expect(buildAssetListPath('component', { owner: 'public', query: 'abc' })).toBe(
      '/v1/components?owner=public&q=abc',
    );
    expect(buildAssetListPath('variant', { owner: 'me' })).toBe('/v1/variants?owner=me');
  });

  it('builds typed management list paths', () => {
    expect(buildManagedAssetListPath('component', { ownerUserId: 'user-1', query: 'abc', includeDeleted: true })).toBe(
      '/v1/management/components?ownerUserId=user-1&q=abc&includeDeleted=true',
    );
    expect(buildManagedAssetListPath('variant', { query: 'graph' })).toBe('/v1/management/variants?q=graph');
  });

  it('builds typed management detail paths', () => {
    expect(buildManagedAssetDetailPath({ assetType: 'component', assetId: 'component-1' })).toBe(
      '/v1/management/components/component-1',
    );
    expect(buildManagedAssetDetailPath({ assetType: 'variant', assetId: 'variant-1' }, { includeDeleted: true })).toBe(
      '/v1/management/variants/variant-1?includeDeleted=true',
    );
  });
});
