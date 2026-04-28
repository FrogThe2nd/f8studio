import { AssetGrid } from '../components/AssetGrid.jsx';
import { EmptyState } from '../components/EmptyState.jsx';
import { SearchBar } from '../components/SearchBar.jsx';
import { Button } from '../components/ui/button.jsx';
import { useAssets } from '../hooks/useAssets.js';

const assetFilters = [
  { value: 'all', label: 'All' },
  { value: 'component', label: 'Components' },
  { value: 'variant', label: 'Variants' },
];

export function BrowseRoute() {
  const {
    assetType,
    entries,
    error,
    hasMore,
    loading,
    loadingMore,
    loadMore,
    query,
    setAssetType,
    setQuery,
  } = useAssets({
    owner: '',
  });

  return (
    <section className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Discovery</p>
          <h2 className="mt-3 text-3xl font-semibold text-white">Browse Assets</h2>
          <p className="mt-2 text-sm text-slate-300">Explore public assets and your own private work from a single catalog.</p>
        </div>
      </header>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
        <SearchBar value={query} onChange={setQuery} placeholder="Search by name, description, tag, or base node type" />
        <div className="flex flex-wrap gap-2">
          {assetFilters.map((filter) => (
            <Button
              key={filter.value}
              type="button"
              variant={assetType === filter.value ? 'default' : 'outline'}
              className={assetType === filter.value ? '' : 'border-white/15 bg-white/5 text-white hover:bg-white/10'}
              onClick={() => setAssetType(filter.value)}
            >
              {filter.label}
            </Button>
          ))}
        </div>
      </div>
      {error ? <p className="text-sm text-rose-200">{error}</p> : null}
      {loading ? <p className="text-sm text-slate-300">Loading assets...</p> : null}
      {!loading && entries.length === 0 ? (
        <EmptyState
          title="Nothing matched this search"
          description="Try a broader query or switch asset types."
        />
      ) : (
        <AssetGrid assets={entries} />
      )}
      {hasMore ? (
        <div className="flex justify-center">
          <Button variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore ? 'Loading more...' : 'Load More'}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
