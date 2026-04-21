import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUseSession } = vi.hoisted(() => ({
  mockUseSession: vi.fn(),
}));

const { mockSignUpEmail } = vi.hoisted(() => ({
  mockSignUpEmail: vi.fn(),
}));

vi.mock('../authClient.js', () => ({
  authClient: {
    signUp: {
      email: (...args) => mockSignUpEmail(...args),
    },
  },
}));

vi.mock('../hooks/useSession.jsx', () => ({
  useSession: () => mockUseSession(),
}));

import { RegisterRoute } from './register.jsx';

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={['/register']}>
      <RegisterRoute />
    </MemoryRouter>,
  );
}

describe('RegisterRoute', () => {
  beforeEach(() => {
    mockUseSession.mockReset();
    mockUseSession.mockReturnValue({
      authResolved: true,
      isAuthenticated: false,
      siteSettings: { allowUserRegistration: true },
    });
    mockSignUpEmail.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('uses the root verify-email callback URL', async () => {
    mockSignUpEmail.mockResolvedValue(undefined);

    renderRoute();

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alice' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } });
    fireEvent.change(screen.getByLabelText('Confirm Password'), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));

    expect(mockSignUpEmail).toHaveBeenCalledWith({
      name: 'Alice',
      email: 'alice@example.com',
      password: 'password123',
      callbackURL: 'http://localhost:3000/verify-email?verified=1',
    });
  });

  it('stops submission when the passwords do not match', () => {
    renderRoute();

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Alice' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password123' } });
    fireEvent.change(screen.getByLabelText('Confirm Password'), { target: { value: 'different' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));

    expect(screen.getByText('Passwords do not match.')).toBeTruthy();
    expect(mockSignUpEmail).not.toHaveBeenCalled();
  });
});
