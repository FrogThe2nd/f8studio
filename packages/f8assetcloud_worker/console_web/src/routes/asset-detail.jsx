import { useEffect, useMemo, useState } from 'react';
import { Info, Layers3, Users } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';

import { Layout } from '../app/Layout.jsx';
import { PublicLayout } from '../app/PublicLayout.jsx';
import { DownloadButton } from '../components/DownloadButton.jsx';
import { EditAssetDialog } from '../components/EditAssetDialog.jsx';
import { EmptyState } from '../components/EmptyState.jsx';
import { ShareDialog } from '../components/ShareDialog.jsx';
import { SubscribeButton } from '../components/SubscribeButton.jsx';
import { TagList } from '../components/TagList.jsx';
import { VersionContentViewer } from '../components/VersionContentViewer.jsx';
import { VersionTimeline } from '../components/VersionTimeline.jsx';
import { Button } from '../components/ui/button.jsx';
import { useAsset } from '../hooks/useAsset.js';
import { useSession } from '../hooks/useSession.jsx';
import { formatTimestamp, formatRelativeVersion } from '../lib/format.js';

const tabs = [
  { value: 'overview', label: 'Overview', icon: Info },
  { value: 'versions', label: 'Versions', icon: Layers3 },
  { value: 'subscribers', label: 'Subscribers', icon: Users },
];

export function AssetDetailRoute() {
  const { assetId = '' } = useParams();
  const { asset, assetType, error, loadVersionContent, loading, setAsset, versionContentByNumber, versions } = useAsset(assetId);
  const { authResolved, currentUser, isAuthenticated } = useSession();
  const [selectedTab, setSelectedTab] = useState('overview');
  const [selectedVersionNumber, setSelectedVersionNumber] = useState(null);

  useEffect(() => {
    if (versions.length > 0) {
      setSelectedVersionNumber(Number(versions[0].versionNumber));
    }
  }, [versions]);

  const selectedVersion = useMemo(
    () => versions.find((entry) => Number(entry.versionNumber) === Number(selectedVersionNumber)) || null,
    [selectedVersionNumber, versions],
  );

  useEffect(() => {
    if (!selectedVersion) {
      return;
    }
    void loadVersionContent(selectedVersion.versionNumber);
  }, [loadVersionContent, selectedVersion]);

  const selectedContent = selectedVersion
    ? versionContentByNumber[String(selectedVersion.versionNumber)] || null
    : null;

  const assetShell = (
    <AssetDetailContent
      asset={asset}
      assetId={assetId}
      assetType={assetType}
      authResolved={authResolved}
      currentUser={currentUser}
      error={error}
      isAuthenticated={isAuthenticated}
      loading={loading}
      selectedContent={selectedContent}
      selectedTab={selectedTab}
      selectedVersion={selectedVersion}
      selectedVersionNumber={selectedVersionNumber}
      setAsset={setAsset}
      setSelectedTab={setSelectedTab}
      setSelectedVersionNumber={setSelectedVersionNumber}
      versions={versions}
    />
  );

  if (isAuthenticated) {
    return <Layout>{assetShell}</Layout>;
  }
  return (
    <PublicLayout title="Asset Viewer" subtitle="Public-facing detail pages for shareable assets.">
      <div className="w-full">{assetShell}</div>
    </PublicLayout>
  );
}

function AssetDetailContent({
  asset,
  assetId,
  assetType,
  authResolved,
  currentUser,
  error,
  isAuthenticated,
  loading,
  selectedContent,
  selectedTab,
  selectedVersion,
  selectedVersionNumber,
  setAsset,
  setSelectedTab,
  setSelectedVersionNumber,
  versions,
}) {
  if (loading || !authResolved) {
    return <p className="text-sm text-slate-300">Loading asset details...</p>;
  }
  if (error || !asset) {
    return (
      <EmptyState
        title="Asset not found"
        description={error || 'This asset may be private, deleted, or no longer available.'}
        action={<Button asChild><Link to={isAuthenticated ? '/browse' : '/login'}>{isAuthenticated ? 'Back to Browse' : 'Sign In'}</Link></Button>}
      />
    );
  }

  const canEdit = Boolean(asset?.editable);
  const activeSubscribers = asset?.subscribed ? 'You are subscribed.' : 'Not subscribed yet.';

  return (
    <section className="space-y-6">
      <header className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="max-w-3xl">
            <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">{assetType}</p>
            <h2 className="mt-3 text-4xl font-semibold text-white">{asset.name}</h2>
            <p className="mt-3 text-base leading-7 text-slate-300">{asset.description || 'No description provided yet.'}</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <span className="rounded-full border border-white/10 bg-slate-950/60 px-3 py-1 text-sm text-slate-100">
                {asset.visibility}
              </span>
              <span className="rounded-full border border-white/10 bg-slate-950/60 px-3 py-1 text-sm text-slate-100">
                Owner: {asset.ownerDisplayName || asset.ownerUserId}
              </span>
              <span className="rounded-full border border-white/10 bg-slate-950/60 px-3 py-1 text-sm text-slate-100">
                {versions.length} version{versions.length === 1 ? '' : 's'}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap gap-3">
            {isAuthenticated ? (
              <SubscribeButton
                assetId={assetId}
                assetType={assetType}
                subscribed={asset.subscribed}
                onSettled={(payload) => setAsset(payload)}
              />
            ) : (
              <Button asChild>
                <Link to="/login">Log In to Subscribe</Link>
              </Button>
            )}
            <ShareDialog assetId={assetId} />
            <DownloadButton assetId={assetId} assetType={assetType} />
            {canEdit ? (
              <EditAssetDialog asset={asset} assetType={assetType} onUpdated={(payload) => setAsset(payload)} />
            ) : null}
          </div>
        </div>
      </header>

      <div className="flex flex-wrap gap-2">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <Button
              key={tab.value}
              type="button"
              variant={selectedTab === tab.value ? 'default' : 'outline'}
              className={selectedTab === tab.value ? '' : 'border-white/15 bg-white/5 text-white hover:bg-white/10'}
              onClick={() => setSelectedTab(tab.value)}
            >
              <Icon className="size-4" />
              {tab.label}
            </Button>
          );
        })}
      </div>

      {selectedTab === 'overview' ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
            <h3 className="text-xl font-semibold text-white">Overview</h3>
            <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-200">{asset.description || 'No markdown body has been published yet.'}</p>
          </section>
          <aside className="space-y-6">
            <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
              <h3 className="text-lg font-semibold text-white">Tags</h3>
              <div className="mt-4">
                <TagList tags={asset.tags} />
              </div>
            </section>
            <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
              <h3 className="text-lg font-semibold text-white">Metadata</h3>
              <dl className="mt-4 space-y-3 text-sm text-slate-300">
                <div className="flex justify-between gap-4">
                  <dt>Version</dt>
                  <dd>{formatRelativeVersion(asset.versionNumber)}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt>Updated</dt>
                  <dd>{formatTimestamp(asset.updatedAt)}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt>Subscribed</dt>
                  <dd>{activeSubscribers}</dd>
                </div>
              </dl>
            </section>
          </aside>
        </div>
      ) : null}

      {selectedTab === 'versions' ? (
        <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="rounded-[2rem] border border-white/10 bg-white/5 p-4">
            <VersionTimeline
              versions={versions}
              selectedVersionNumber={selectedVersionNumber}
              onSelect={(version) => setSelectedVersionNumber(Number(version.versionNumber))}
            />
          </aside>
          <section className="space-y-4 rounded-[2rem] border border-white/10 bg-white/5 p-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h3 className="text-xl font-semibold text-white">
                  {selectedVersion ? formatRelativeVersion(selectedVersion.versionNumber) : 'Select a version'}
                </h3>
                {selectedVersion ? (
                  <p className="mt-2 text-sm text-slate-300">{selectedVersion.changeSummary || 'No change summary recorded.'}</p>
                ) : null}
              </div>
              {selectedVersion ? (
                <DownloadButton assetId={assetId} assetType={assetType} versionNumber={selectedVersion.versionNumber}>
                  Download {formatRelativeVersion(selectedVersion.versionNumber)}
                </DownloadButton>
              ) : null}
            </div>
            <VersionContentViewer content={selectedContent?.record || null} />
          </section>
        </div>
      ) : null}

      {selectedTab === 'subscribers' ? (
        <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
          {currentUser?.userId === asset.ownerUserId ? (
            <>
              <h3 className="text-xl font-semibold text-white">Subscribers</h3>
              <p className="mt-3 text-sm leading-6 text-slate-300">
                Subscriber listing is owner-only. The backend exposes subscription state, and this panel is ready for the next pass that surfaces the full roster.
              </p>
            </>
          ) : (
            <EmptyState
              title="Subscribers are private"
              description="Only the asset owner can view the subscriber roster."
            />
          )}
        </section>
      ) : null}
    </section>
  );
}
