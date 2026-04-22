import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockCopyToClipboard } = vi.hoisted(() => ({
  mockCopyToClipboard: vi.fn(),
}));

vi.mock('../lib/clipboard.js', () => ({
  copyToClipboard: (...args) => mockCopyToClipboard(...args),
}));

import { ShareDialog } from './ShareDialog.jsx';

describe('ShareDialog', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockCopyToClipboard.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it('copies the public asset link immediately from the share button', async () => {
    mockCopyToClipboard.mockResolvedValue(undefined);

    render(<ShareDialog assetId="asset-123" />);

    fireEvent.click(screen.getByRole('button', { name: 'Share' }));

    await Promise.resolve();
    await Promise.resolve();

    expect(mockCopyToClipboard).toHaveBeenCalledWith('http://localhost:3000/assets/asset-123');
    expect(screen.getByRole('button', { name: 'Link Copied' })).toBeTruthy();

    await vi.advanceTimersByTimeAsync(1800);

    expect(screen.getByRole('button', { name: 'Share' })).toBeTruthy();
  });
});
