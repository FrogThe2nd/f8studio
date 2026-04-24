import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUpdateAssetVersionNote } = vi.hoisted(() => ({
  mockUpdateAssetVersionNote: vi.fn(),
}));

vi.mock('../lib/api.js', () => ({
  updateAssetVersionNote: (...args) => mockUpdateAssetVersionNote(...args),
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

import { EditVersionNoteDialog } from './EditVersionNoteDialog.jsx';

const version = {
  versionNumber: 3,
  changeSummary: 'Old note',
};

describe('EditVersionNoteDialog', () => {
  beforeEach(() => {
    mockUpdateAssetVersionNote.mockReset();
    mockUpdateAssetVersionNote.mockResolvedValue({
      versionNumber: 3,
      changeSummary: 'Updated **note**',
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('submits Markdown version notes as changeSummary', async () => {
    const onUpdated = vi.fn();
    render(
      <EditVersionNoteDialog
        assetId="asset-1"
        assetType="component"
        version={version}
        onUpdated={onUpdated}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit Notes' }));
    fireEvent.change(await screen.findByLabelText('Version notes'), {
      target: { value: '## Release\n\nFixed **timing**.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Notes' }));

    await waitFor(() => {
      expect(mockUpdateAssetVersionNote).toHaveBeenCalledWith('component', 'asset-1', 3, {
        changeSummary: '## Release\n\nFixed **timing**.',
      });
    });
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({
      changeSummary: 'Updated **note**',
    }));
  });

  it('keeps blank notes as null for the existing API contract', async () => {
    render(
      <EditVersionNoteDialog
        assetId="asset-1"
        assetType="variant"
        version={version}
        onUpdated={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit Notes' }));
    fireEvent.change(await screen.findByLabelText('Version notes'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Notes' }));

    await waitFor(() => {
      expect(mockUpdateAssetVersionNote).toHaveBeenCalledWith('variant', 'asset-1', 3, {
        changeSummary: null,
      });
    });
  });
});
