import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockRequestPasswordReset } = vi.hoisted(() => ({
  mockRequestPasswordReset: vi.fn(),
}));

vi.mock('../authClient.js', () => ({
  authClient: {
    requestPasswordReset: (...args) => mockRequestPasswordReset(...args),
  },
}));

import { ForgotPasswordRoute } from './forgot-password.jsx';

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={['/forgot-password']}>
      <ForgotPasswordRoute />
    </MemoryRouter>,
  );
}

describe('ForgotPasswordRoute', () => {
  beforeEach(() => {
    mockRequestPasswordReset.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('uses the root reset-password redirect URL', async () => {
    mockRequestPasswordReset.mockResolvedValue(undefined);

    renderRoute();

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send Reset Link' }));

    expect(mockRequestPasswordReset).toHaveBeenCalledWith({
      email: 'alice@example.com',
      redirectTo: 'http://localhost:3000/reset-password',
    });
  });
});
