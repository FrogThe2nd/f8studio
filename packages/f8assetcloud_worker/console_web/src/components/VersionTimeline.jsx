import { formatRelativeVersion, formatTimestamp } from '../lib/format.js';
import { cn } from '../lib/cn.js';

export function VersionTimeline({ versions, selectedVersionNumber, onSelect }) {
  return (
    <div className="space-y-3">
      {(versions || []).map((version) => {
        const selected = Number(version?.versionNumber) === Number(selectedVersionNumber);
        return (
          <button
            key={String(version?.versionNumber)}
            type="button"
            onClick={() => onSelect(version)}
            className={cn(
              'w-full rounded-2xl border p-4 text-left transition',
              selected
                ? 'border-cyan-300/35 bg-cyan-300/12'
                : 'border-white/10 bg-white/5 hover:border-white/20 hover:bg-white/8',
            )}
          >
            <p className="text-sm font-semibold text-white">{formatRelativeVersion(version?.versionNumber)}</p>
            <p className="mt-1 text-xs uppercase tracking-[0.2em] text-cyan-200/70">{version?.revision || 'revision'}</p>
            <p className="mt-3 text-sm text-slate-300">{version?.changeSummary || 'No change summary recorded.'}</p>
            <p className="mt-3 text-xs text-slate-400">{formatTimestamp(version?.createdAt)}</p>
          </button>
        );
      })}
    </div>
  );
}
