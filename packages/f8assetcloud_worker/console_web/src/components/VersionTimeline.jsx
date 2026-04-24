import { formatRelativeVersion, formatTimestamp, summarizeDescription } from '../lib/format.js';
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
            {version?.changeSummary ? (
              <p className="mt-3 text-sm leading-6 text-slate-300">{summarizeDescription(version.changeSummary)}</p>
            ) : null}
            <p className="mt-3 text-xs text-slate-400">{formatTimestamp(version?.createdAt)}</p>
          </button>
        );
      })}
    </div>
  );
}
