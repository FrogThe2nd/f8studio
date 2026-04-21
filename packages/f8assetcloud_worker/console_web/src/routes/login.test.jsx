import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUseSession } = vi.hoisted(() => ({
  mockUseSession: vi.fn(),
}));

const { mockSignInEmail, mockSignInSocial } = vi.hoisted(() => ({
  mockSignInEmail: vi.fn(),
  mockSignInSocial: vi.fn(),
}));

vi.mock('../authClient.js', () => ({
  authClient: {
    signIn: {
      email: (...args) => mockSignInEmail(...args),
      social: (...args) => mockSignInSocial(...args),
    },
  },
}));

vi.mock('../hooks/useSession.jsx', () => ({
  useSession: () => mockUseSession(),
}));

import { LoginRoute } from './login.jsx';

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <LoginRoute />
    </MemoryRouter>,
  );
}

describe('LoginRoute', () => {
  beforeEach(() => {
    mockUseSession.mockReset();
    mockSignInEmail.mockReset();
    mockSignInSocial.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('hides Google and registration entry points when public registration is disabled', () => {
    mockUseSession.mockReturnValue({
      authProviders: { google: true },
      authResolved: true,
      isAuthenticated: false,
      siteSettings: { allowUserRegistration: false },
    });

    renderRoute();

    expect(screen.getByText('Public registration is currently disabled. Sign in with an existing email/password account.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Continue with Google' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Create account' })).toBeNull();
  });

  it('uses a relative callback URL for Google sign-in', async () => {
    mockUseSession.mockReturnValue({
      authProviders: { google: true },
      authResolved: true,
      isAuthenticated: false,
      siteSettings: { allowUserRegistration: true },
    });
    mockSignInSocial.mockResolvedValue(undefined);

    renderRoute();

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }));

    expect(mockSignInSocial).toHaveBeenCalledWith({
      provider: 'google',
      callbackURL: '/assets/mine',
    });
  });
});
