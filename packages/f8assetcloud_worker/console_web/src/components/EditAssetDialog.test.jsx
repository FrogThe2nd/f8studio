import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUpdateAssetMeta } = vi.hoisted(() => ({
  mockUpdateAssetMeta: vi.fn(),
}));

vi.mock('../lib/api.js', () => ({
  updateAssetMeta: (...args) => mockUpdateAssetMeta(...args),
}));

vi.mock('./MarkdownEditor.jsx', () => ({
  MarkdownEditor: ({ label, onChange, placeholder, value }) => (
    <label>
      {label}
      <textarea
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
    </label>
  ),
}));

import { EditAssetDialog } from './EditAssetDialog.jsx';

const asset = {
  assetId: 'asset-1',
  name: 'Existing asset',
  description: 'Old **description**',
  tags: ['alpha', 'beta'],
};

describe('EditAssetDialog', () => {
  beforeEach(() => {
    mockUpdateAssetMeta.mockReset();
    mockUpdateAssetMeta.mockResolvedValue({
      ...asset,
      description: 'Updated **description**',
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('submits the Markdown description string without changing the API payload shape', async () => {
    const onUpdated = vi.fn();
    render(<EditAssetDialog asset={asset} assetType="variant" onUpdated={onUpdated} />);

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(await screen.findByLabelText('Description'), {
      target: { value: '# Updated\n\n- Supports **Markdown**' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(mockUpdateAssetMeta).toHaveBeenCalledWith('variant', 'asset-1', {
        name: 'Existing asset',
        description: '# Updated\n\n- Supports **Markdown**',
        tags: ['alpha', 'beta'],
      });
    });
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({
      description: 'Updated **description**',
    }));
  });
});
