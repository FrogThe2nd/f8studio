export function EmptyState({ title, description, action }) {
  return (
    <div className="rounded-[2rem] border border-dashed border-white/15 bg-white/5 p-8 text-center">
      <h2 className="text-xl font-semibold text-white">{title}</h2>
      <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-slate-300">{description}</p>
      {action ? <div className="mt-6 flex justify-center">{action}</div> : null}
    </div>
  );
}
