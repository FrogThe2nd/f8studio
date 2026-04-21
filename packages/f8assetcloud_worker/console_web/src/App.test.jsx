import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
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
      email: vi.fn(),
      social: vi.fn(),
    },
    signUp: {
      email: vi.fn(),
    },
    requestPasswordReset: vi.fn(),
    changePassword: vi.fn(),
  },
}));

import * as AppModule from './App.jsx';

const {
  buildAssetListPath,
  buildManagedAssetDetailPath,
  buildManagedAssetListPath,
  ConsoleRootApp,
  downloadableContentForAsset,
  formatTimestampForDisplay,
  formatTimestampTooltip,
} = AppModule;

function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
  });
}

describe('ConsoleRootApp desktop auth callback routes', () => {
  beforeEach(() => {
    mockRefetch.mockReset();
    mockSignOut.mockReset();
    mockUseSession.mockReset();
    mockUseSession.mockReturnValue({
      data: null,
      isPending: false,
      refetch: mockRefetch,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders a friendly completion page for desktop browser sign-in and redirects back to the console', async () => {
    vi.useFakeTimers();
    window.history.replaceState({}, '', '/console/auth-complete');
    const assignSpy = vi.spyOn(AppModule.appNavigation, 'navigateToConsoleHome').mockImplementation(() => {});

    render(<ConsoleRootApp />);

    expect(screen.getByText('Desktop Sign-In Complete')).toBeTruthy();
    expect(screen.getByText('Your browser sign-in finished. You can return to PyStudio now.')).toBeTruthy();
    expect(screen.getByText('You can close this tab if it did not close automatically.')).toBeTruthy();
    expect(screen.getByText('This page will return to the console automatically in a moment.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Console' }).getAttribute('href')).toBe('/console/');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    expect(assignSpy).toHaveBeenCalledTimes(1);
  });

  it('renders a helpful error page for desktop browser sign-in failures', () => {
    window.history.replaceState({}, '', '/console/auth-error?error=access_denied&error_description=User%20cancelled');

    render(<ConsoleRootApp />);

    expect(screen.getByText('Desktop Sign-In Needs Attention')).toBeTruthy();
    expect(screen.getByText('The browser sign-in did not finish cleanly. Return to PyStudio and try again if needed.')).toBeTruthy();
    expect(screen.getByText('access_denied: User cancelled')).toBeTruthy();
    expect(screen.getByText('If PyStudio still shows a login prompt, start the browser sign-in again.')).toBeTruthy();
  });
});

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
          name: 'Alice',
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
    expect(screen.getByText('Account Overview')).toBeTruthy();
    expect(screen.getAllByText('Email Verified').length).toBeGreaterThan(0);
    expect(screen.getByText(/alice@example\.com/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save Name' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change Email' })).toBeTruthy();
  });

  it('updates the profile name through the current-user endpoint', async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input.url);
      const method = String(init?.method || 'GET').toUpperCase();
      if (url === '/v1/auth/providers') {
        return jsonResponse({ google: false });
      }
      if (url === '/v1/site-settings') {
        return jsonResponse({ allowUserRegistration: false });
      }
      if (url === '/v1/me' && method === 'GET') {
        return jsonResponse({
          userId: 'user-1',
          name: 'Alice',
          email: 'alice@example.com',
          emailVerified: true,
          role: 'user',
          isAdmin: false,
        });
      }
      if (url === '/v1/me' && method === 'PUT') {
        expect(JSON.parse(String(init?.body || '{}'))).toEqual({ name: 'Alice Cooper' });
        return jsonResponse({
          userId: 'user-1',
          name: 'Alice Cooper',
          email: 'alice@example.com',
          emailVerified: true,
          role: 'user',
          isAdmin: false,
        });
      }
      if (url === '/api/auth/list-accounts') {
        return jsonResponse([{ providerId: 'credential', accountId: 'alice' }]);
      }
      throw new Error(`Unexpected fetch: ${url} (${method})`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ConsoleRootApp />);

    expect(await screen.findByText('Welcome, Alice')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alice Cooper' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Name' }));

    expect(await screen.findByText('Welcome, Alice Cooper')).toBeTruthy();
    expect(screen.getByText('Name updated')).toBeTruthy();
    expect(mockRefetch).toHaveBeenCalled();
  });

  it('submits an email change request from the profile page', async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input.url);
      const method = String(init?.method || 'GET').toUpperCase();
      if (url === '/v1/auth/providers') {
        return jsonResponse({ google: false });
      }
      if (url === '/v1/site-settings') {
        return jsonResponse({ allowUserRegistration: false });
      }
      if (url === '/v1/me' && method === 'GET') {
        return jsonResponse({
          userId: 'user-1',
          name: 'Alice',
          email: 'alice@example.com',
          emailVerified: true,
          role: 'user',
          isAdmin: false,
        });
      }
      if (url === '/api/auth/change-email' && method === 'POST') {
        expect(JSON.parse(String(init?.body || '{}'))).toEqual({
          newEmail: 'alice.new@example.com',
          callbackURL: 'http://localhost:3000/console/verify-email?verified=1',
        });
        return jsonResponse({ status: true });
      }
      if (url === '/api/auth/list-accounts') {
        return jsonResponse([{ providerId: 'credential', accountId: 'alice' }]);
      }
      throw new Error(`Unexpected fetch: ${url} (${method})`);
    });
    vi.stubGlobal('fetch', fetchMock);
    window.history.replaceState({}, '', '/console/');

    render(<ConsoleRootApp />);

    expect(await screen.findByText('Welcome, Alice')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice.new@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change Email' }));

    expect(await screen.findByText('If alice.new@example.com is available, a verification link has been sent there.')).toBeTruthy();
    expect(mockRefetch).toHaveBeenCalled();
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

describe('formatTimestampForDisplay', () => {
  it('formats ISO timestamps in the browser local timezone', () => {
    const formatterSpy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(function MockDateTimeFormat() {
      return {
        format: () => '04/15/2026, 09:45:00',
      };
    });

    expect(formatTimestampForDisplay('2026-04-15T13:45:00Z')).toBe('04/15/2026, 09:45:00');
    expect(formatTimestampForDisplay('')).toBe('');

    formatterSpy.mockRestore();
  });

  it('returns the original text when the timestamp is invalid', () => {
    expect(formatTimestampForDisplay('not-a-timestamp')).toBe('not-a-timestamp');
  });
});

describe('formatTimestampTooltip', () => {
  it('includes the local timezone abbreviation in the tooltip text', () => {
    const formatterSpy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(function MockDateTimeFormat(
      _locale,
      options,
    ) {
      if (options?.timeZoneName === 'short') {
        return {
          formatToParts: () => [
            { type: 'month', value: '04' },
            { type: 'literal', value: '/' },
            { type: 'day', value: '15' },
            { type: 'literal', value: '/' },
            { type: 'year', value: '2026' },
            { type: 'literal', value: ', ' },
            { type: 'hour', value: '09' },
            { type: 'literal', value: ':' },
            { type: 'minute', value: '45' },
            { type: 'literal', value: ':' },
            { type: 'second', value: '00' },
            { type: 'literal', value: ' ' },
            { type: 'timeZoneName', value: 'EDT' },
          ],
        };
      }
      return {
        format: () => '04/15/2026, 09:45:00',
      };
    });

    expect(formatTimestampTooltip('2026-04-15T13:45:00Z')).toBe('04/15/2026, 09:45:00 EDT');
    expect(formatTimestampTooltip('')).toBe('');

    formatterSpy.mockRestore();
  });

  it('returns the original text when the timestamp is invalid', () => {
    expect(formatTimestampTooltip('not-a-timestamp')).toBe('not-a-timestamp');
  });
});
