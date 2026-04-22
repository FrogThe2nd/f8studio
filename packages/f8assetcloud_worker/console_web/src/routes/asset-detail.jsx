import { Suspense, lazy, useEffect, useState } from 'react';
import { Info, Layers3, Users } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';

import { Layout } from '../app/Layout.jsx';
import { PublicLayout } from '../app/PublicLayout.jsx';
import { DownloadButton } from '../components/DownloadButton.jsx';
import { EmptyState } from '../components/EmptyState.jsx';
import { ShareDialog } from '../components/ShareDialog.jsx';
import { SubscribeButton } from '../components/SubscribeButton.jsx';
import { TagList } from '../components/TagList.jsx';
import { VersionTimeline } from '../components/VersionTimeline.jsx';
import { Button } from '../components/ui/button.jsx';
import { useAsset } from '../hooks/useAsset.js';
import { useSession } from '../hooks/useSession.jsx';
import { getAssetSubscribers } from '../lib/api.js';
import { formatTimestamp, formatRelativeVersion, summarizeDescription } from '../lib/format.js';

function lazyNamedComponent(loader, exportName) {
  return lazy(async () => {
    const module = await loader();
    return {
      default: module[exportName],
    };
  });
}

const EditAssetDialog = lazyNamedComponent(() => import('../components/EditAssetDialog.jsx'), 'EditAssetDialog');
const EditVersionNoteDialog = lazyNamedComponent(() => import('../components/EditVersionNoteDialog.jsx'), 'EditVersionNoteDialog');
const MarkdownContent = lazyNamedComponent(() => import('../components/MarkdownContent.jsx'), 'MarkdownContent');

function InlineLoader({ label }) {
  return <p className="text-sm text-slate-400">{label}</p>;
}

function LazyPanel({ children, fallback = null }) {
  return (
    <Suspense fallback={fallback}>
      {children}
    </Suspense>
  );
}

export function AssetDetailRoute() {
  const { assetId = '' } = useParams();
  const { asset, assetType, error, loading, setAsset, setVersions, versions } = useAsset(assetId);
  const { authResolved, currentUser, isAuthenticated } = useSession();
  const [selectedTab, setSelectedTab] = useState('overview');
  const [selectedVersionNumber, setSelectedVersionNumber] = useState(null);
  const [subscribers, setSubscribers] = useState([]);
  const [subscribersLoading, setSubscribersLoading] = useState(false);
  const [subscribersError, setSubscribersError] = useState('');

  useEffect(() => {
    if (versions.length > 0) {
      setSelectedVersionNumber(Number(versions[0].versionNumber));
    }
  }, [versions]);

  useEffect(() => {
    setSubscribers([]);
    setSubscribersError('');
    setSubscribersLoading(false);
  }, [assetId]);

  const selectedVersion = versions.find((entry) => Number(entry.versionNumber) === Number(selectedVersionNumber)) || null;
  const canViewSubscribers = currentUser?.userId === asset?.ownerUserId;

  useEffect(() => {
    if (!assetId || !assetType || !canViewSubscribers || selectedTab !== 'subscribers') {
      return;
    }
    let cancelled = false;
    setSubscribersLoading(true);
    setSubscribersError('');
    void (async () => {
      try {
        const payload = await getAssetSubscribers(assetType, assetId);
        if (cancelled) {
          return;
        }
        setSubscribers(Array.isArray(payload?.entries) ? payload.entries : []);
      } catch (errorValue) {
        if (cancelled) {
          return;
        }
        setSubscribersError(errorValue instanceof Error ? errorValue.message : String(errorValue));
      } finally {
        if (!cancelled) {
          setSubscribersLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [assetId, assetType, canViewSubscribers, selectedTab]);

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
      selectedTab={selectedTab}
      selectedVersion={selectedVersion}
      selectedVersionNumber={selectedVersionNumber}
      setAsset={setAsset}
      setSelectedTab={setSelectedTab}
      setSelectedVersionNumber={setSelectedVersionNumber}
      setVersions={setVersions}
      subscribers={subscribers}
      subscribersError={subscribersError}
      subscribersLoading={subscribersLoading}
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
  selectedTab,
  selectedVersion,
  selectedVersionNumber,
  setAsset,
  setSelectedTab,
  setSelectedVersionNumber,
  setVersions,
  subscribers,
  subscribersError,
  subscribersLoading,
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
  const canSubscribe = Boolean(isAuthenticated);
  const activeSubscribers = asset?.subscribed ? 'You are subscribed.' : 'Not subscribed yet.';
  const canViewSubscribers = currentUser?.userId === asset.ownerUserId;
  const tabs = [
    { value: 'overview', label: 'Overview', icon: Info },
    { value: 'versions', label: 'Versions', icon: Layers3 },
    ...(canViewSubscribers ? [{ value: 'subscribers', label: 'Subscribers', icon: Users }] : []),
  ];

  return (
    <section className="space-y-6">
      <header className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="max-w-3xl">
            <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">{assetType}</p>
            <h2 className="mt-3 text-4xl font-semibold text-white">{asset.name}</h2>
            <p className="mt-3 text-base leading-7 text-slate-300">{summarizeDescription(asset.description)}</p>
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
            {canSubscribe ? (
              <SubscribeButton
                assetId={assetId}
                assetType={assetType}
                subscribed={asset.subscribed}
                onSettled={(payload) => setAsset(payload)}
              />
            ) : !isAuthenticated ? (
              <Button asChild>
                <Link to="/login">Log In to Subscribe</Link>
              </Button>
            ) : null}
            <ShareDialog assetId={assetId} />
            <DownloadButton assetId={assetId} assetType={assetType} />
            {canEdit ? (
              <LazyPanel>
                <EditAssetDialog asset={asset} assetType={assetType} onUpdated={(payload) => setAsset(payload)} />
              </LazyPanel>
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
            <LazyPanel fallback={<InlineLoader label="Loading markdown..." />}>
              <MarkdownContent className="mt-4" source={asset.description} placeholder="No markdown body has been published yet." />
            </LazyPanel>
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
              <div className="min-h-6 text-sm text-slate-300">
                {selectedVersion?.changeSummary ? selectedVersion.changeSummary : ''}
              </div>
              <div className="flex flex-wrap gap-3">
                {selectedVersion ? (
                  <DownloadButton assetId={assetId} assetType={assetType} versionNumber={selectedVersion.versionNumber}>
                    Download {formatRelativeVersion(selectedVersion.versionNumber)}
                  </DownloadButton>
                ) : null}
                {canEdit && selectedVersion ? (
                  <LazyPanel>
                    <EditVersionNoteDialog
                      assetId={assetId}
                      assetType={assetType}
                      version={selectedVersion}
                      onUpdated={(payload) => {
                        setVersions((previous) => previous.map((entry) => (
                          Number(entry.versionNumber) === Number(payload.versionNumber)
                            ? { ...entry, changeSummary: payload.changeSummary }
                            : entry
                        )));
                        setAsset((previous) => (
                          previous && Number(previous.versionNumber) === Number(payload.versionNumber)
                            ? { ...previous, changeSummary: payload.changeSummary }
                            : previous
                        ));
                      }}
                    />
                  </LazyPanel>
                ) : null}
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {selectedTab === 'subscribers' && canViewSubscribers ? (
        <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
          <h3 className="text-xl font-semibold text-white">Subscribers</h3>
          {subscribersLoading ? (
            <p className="mt-3 text-sm leading-6 text-slate-300">Loading subscribers...</p>
          ) : null}
          {subscribersError ? (
            <p className="mt-3 text-sm leading-6 text-rose-200">{subscribersError}</p>
          ) : null}
          {!subscribersLoading && !subscribersError && subscribers.length === 0 ? (
            <p className="mt-3 text-sm leading-6 text-slate-300">No subscribers yet.</p>
          ) : null}
          {!subscribersLoading && !subscribersError && subscribers.length > 0 ? (
            <div className="mt-5 space-y-3">
              {subscribers.map((subscriber) => (
                <div key={String(subscriber.userId)} className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-white/10 bg-slate-950/35 px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-white">{subscriber.name}</p>
                    <p className="mt-1 text-xs text-slate-400">{subscriber.email || subscriber.userId}</p>
                  </div>
                  <div className="text-right text-xs text-slate-400">
                    <p>Subscribed {formatTimestamp(subscriber.subscribedAt)}</p>
                    <p className="mt-1">
                      Last seen {subscriber.lastSeenVersionNumber ? formatRelativeVersion(subscriber.lastSeenVersionNumber) : 'Never'}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}
