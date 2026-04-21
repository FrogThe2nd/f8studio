export function TagList({ tags }) {
  const entries = Array.isArray(tags) ? tags.filter(Boolean) : [];
  if (entries.length === 0) {
    return <span className="text-sm text-slate-400">No tags</span>;
  }
  return (
    <div className="flex flex-wrap gap-2">
      {entries.map((tag) => (
        <span key={tag} className="rounded-full border border-white/12 bg-white/8 px-3 py-1 text-xs font-medium uppercase tracking-[0.18em] text-cyan-100">
          {tag}
        </span>
      ))}
    </div>
  );
}
