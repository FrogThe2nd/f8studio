import { Download, Eye, Layers3 } from 'lucide-react';
import { Link } from 'react-router-dom';

import { TagList } from './TagList.jsx';
import { Button } from './ui/button.jsx';
import { formatTimestamp, summarizeDescription } from '../lib/format.js';

export function AssetCard({ asset }) {
  const assetId = String(asset?.assetId || asset?.componentId || asset?.variantId || '');
  return (
    <article className="group flex h-full flex-col rounded-[1.75rem] border border-white/10 bg-white/6 p-5 shadow-[0_22px_65px_rgba(0,0,0,0.24)] transition hover:-translate-y-1 hover:border-cyan-300/25 hover:bg-white/8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-cyan-200/70">{asset?.assetType || 'asset'}</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{asset?.name || assetId}</h3>
        </div>
        <span className="rounded-full border border-white/10 bg-slate-950/55 px-3 py-1 text-xs text-slate-200">
          {asset?.visibility || 'private'}
        </span>
      </div>
      <p className="mt-4 min-h-12 text-sm leading-6 text-slate-300">{summarizeDescription(asset?.description)}</p>
      <div className="mt-4">
        <TagList tags={asset?.tags} />
      </div>
      <dl className="mt-5 grid gap-3 text-sm text-slate-300 sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-[0.24em] text-slate-400">Owner</dt>
          <dd className="mt-1">{asset?.ownerDisplayName || asset?.ownerUserId || 'Unknown'}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-[0.24em] text-slate-400">Updated</dt>
          <dd className="mt-1">{formatTimestamp(asset?.updatedAt)}</dd>
        </div>
      </dl>
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Button asChild size="sm">
          <Link to={`/assets/${encodeURIComponent(assetId)}`}>
            <Eye className="size-4" />
            Open
          </Link>
        </Button>
        <span className="inline-flex items-center gap-2 text-sm text-slate-300">
          <Layers3 className="size-4 text-cyan-200" />
          {Number.isFinite(Number(asset?.versionNumber)) ? `Version ${Number(asset.versionNumber)}` : 'No versions'}
        </span>
        {asset?.subscribed ? (
          <span className="inline-flex items-center gap-2 text-sm text-emerald-200">
            <Download className="size-4" />
            Subscribed
          </span>
        ) : null}
      </div>
    </article>
  );
}
