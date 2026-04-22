export function VersionContentViewer({ version }) {
  if (!version) {
    return (
      <div className="rounded-[1.75rem] border border-dashed border-white/15 bg-white/5 p-6 text-sm text-slate-300">
        Select a version to download its full payload.
      </div>
    );
  }
  return (
    <div className="rounded-[1.75rem] border border-white/10 bg-slate-950/40 p-5">
      <h4 className="text-sm font-semibold uppercase tracking-[0.28em] text-cyan-200/80">Download Only</h4>
      <p className="mt-3 text-sm leading-7 text-slate-300">
        Version payloads can be large, so this page stays compact and does not inline the full JSON anymore.
        Use the download action above to inspect version {Number(version.versionNumber)} locally.
      </p>
    </div>
  );
}
