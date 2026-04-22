import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockUseAsset, mockUseSession, mockLoadVersionContent, mockSetAsset, mockSetVersions } = vi.hoisted(() => ({
  mockUseAsset: vi.fn(),
  mockUseSession: vi.fn(),
  mockLoadVersionContent: vi.fn(),
  mockSetAsset: vi.fn(),
  mockSetVersions: vi.fn(),
}));

vi.mock('../app/Layout.jsx', () => ({
  Layout: ({ children }) => <div data-testid="layout-shell">{children}</div>,
}));

vi.mock('../app/PublicLayout.jsx', () => ({
  PublicLayout: ({ children }) => <div data-testid="public-layout-shell">{children}</div>,
}));

vi.mock('../components/DownloadButton.jsx', () => ({
  DownloadButton: ({ children }) => <button type="button">{children || 'Download'}</button>,
}));

vi.mock('../components/EditAssetDialog.jsx', () => ({
  EditAssetDialog: () => <div data-testid="edit-asset-dialog">Edit asset</div>,
}));

vi.mock('../components/EditVersionNoteDialog.jsx', () => ({
  EditVersionNoteDialog: () => <div data-testid="edit-version-note-dialog">Edit version note</div>,
}));

vi.mock('../components/EmptyState.jsx', () => ({
  EmptyState: ({ title, description, action }) => (
    <div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

vi.mock('../components/MarkdownContent.jsx', () => ({
  MarkdownContent: ({ source, placeholder }) => <div>{source || placeholder}</div>,
}));

vi.mock('../components/ShareDialog.jsx', () => ({
  ShareDialog: () => <button type="button">Share</button>,
}));

vi.mock('../components/SubscribeButton.jsx', () => ({
  SubscribeButton: () => <button type="button">Subscribe</button>,
}));

vi.mock('../components/TagList.jsx', () => ({
  TagList: ({ tags }) => <div>{Array.isArray(tags) ? tags.join(', ') : ''}</div>,
}));

vi.mock('../components/VersionTimeline.jsx', () => ({
  VersionTimeline: () => <div>Version timeline</div>,
}));

vi.mock('../hooks/useAsset.js', () => ({
  useAsset: (...args) => mockUseAsset(...args),
}));

vi.mock('../hooks/useSession.jsx', () => ({
  useSession: () => mockUseSession(),
}));

import { AssetDetailRoute } from './asset-detail.jsx';

const defaultAsset = {
  assetId: 'asset-1',
  name: 'Public Demo Asset',
  description: 'A shared asset for testing.',
  visibility: 'public',
  ownerDisplayName: 'Owner Name',
  ownerUserId: 'owner-1',
  versionNumber: 1,
  updatedAt: '2026-04-21T10:00:00.000Z',
  subscribed: false,
  editable: false,
  tags: ['shared', 'demo'],
};

const defaultVersions = [
  {
    versionNumber: 1,
    changeSummary: 'Initial publish',
  },
];

function renderRoute(entry = '/assets/asset-1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/assets/:assetId" element={<AssetDetailRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AssetDetailRoute', () => {
  beforeEach(() => {
    mockSetAsset.mockReset();
    mockSetVersions.mockReset();
    mockLoadVersionContent.mockReset();
    mockLoadVersionContent.mockResolvedValue({ record: { schemaVersion: 'spec/1' } });
    mockUseAsset.mockReset();
    mockUseAsset.mockReturnValue({
      asset: defaultAsset,
      assetType: 'variant',
      error: '',
      loadVersionContent: mockLoadVersionContent,
      loading: false,
      setAsset: mockSetAsset,
      setVersions: mockSetVersions,
      versionContentByNumber: {
        '1': { record: { schemaVersion: 'spec/1' } },
      },
      versions: defaultVersions,
    });
    mockUseSession.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders a public asset for unauthenticated visitors', () => {
    mockUseSession.mockReturnValue({
      authResolved: true,
      currentUser: null,
      isAuthenticated: false,
    });

    renderRoute();

    expect(screen.getByTestId('public-layout-shell')).toBeTruthy();
    expect(screen.getByText('Public Demo Asset')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Log In to Subscribe' }).getAttribute('href')).toBe('/login');
    expect(screen.queryByTestId('edit-asset-dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Subscribers' })).toBeNull();
    expect(mockLoadVersionContent).not.toHaveBeenCalled();
  });

  it('shows subscribe actions and hides edit controls for non-owners', () => {
    mockUseSession.mockReturnValue({
      authResolved: true,
      currentUser: {
        userId: 'viewer-1',
      },
      isAuthenticated: true,
    });
    mockUseAsset.mockReturnValue({
      asset: {
        ...defaultAsset,
        editable: false,
      },
      assetType: 'component',
      error: '',
      loadVersionContent: mockLoadVersionContent,
      loading: false,
      setAsset: mockSetAsset,
      setVersions: mockSetVersions,
      versionContentByNumber: {
        '1': { record: { schemaVersion: 'f8studio-session/1' } },
      },
      versions: defaultVersions,
    });

    renderRoute();

    expect(screen.getByTestId('layout-shell')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Subscribe' })).toBeTruthy();
    expect(screen.queryByTestId('edit-asset-dialog')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Subscribers' })).toBeNull();
    expect(mockLoadVersionContent).not.toHaveBeenCalled();
  });

  it('shows subscribe controls for the asset owner and keeps owner-only tools visible', async () => {
    mockUseSession.mockReturnValue({
      authResolved: true,
      currentUser: {
        userId: 'owner-1',
      },
      isAuthenticated: true,
    });
    mockUseAsset.mockReturnValue({
      asset: {
        ...defaultAsset,
        editable: true,
        isOwner: true,
      },
      assetType: 'variant',
      error: '',
      loadVersionContent: mockLoadVersionContent,
      loading: false,
      setAsset: mockSetAsset,
      setVersions: mockSetVersions,
      versionContentByNumber: {},
      versions: defaultVersions,
    });

    renderRoute();

    expect(screen.getByRole('button', { name: 'Subscribe' })).toBeTruthy();
    expect(await screen.findByTestId('edit-asset-dialog')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Subscribers' })).toBeTruthy();
  });
});
