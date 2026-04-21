export function VersionContentViewer({ content }) {
  if (!content) {
    return (
      <div className="rounded-[1.75rem] border border-dashed border-white/15 bg-white/5 p-6 text-sm text-slate-300">
        Select a version to inspect its JSON payload.
      </div>
    );
  }
  return (
    <div className="rounded-[1.75rem] border border-white/10 bg-slate-950/70 p-4">
      <pre className="overflow-auto text-xs leading-6 text-cyan-100">
        {JSON.stringify(content, null, 2)}
      </pre>
    </div>
  );
}
