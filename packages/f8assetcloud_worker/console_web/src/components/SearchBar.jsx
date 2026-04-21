import { Search } from 'lucide-react';

export function SearchBar({ value, onChange, placeholder = 'Search assets' }) {
  return (
    <label className="relative block">
      <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="w-full rounded-2xl border border-white/12 bg-slate-950/55 py-3 pl-11 pr-4 text-sm text-white placeholder:text-slate-500 focus:border-cyan-300/40 focus:outline-none"
      />
    </label>
  );
}
