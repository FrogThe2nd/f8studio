import { useEffect, useState } from 'react';

import { listAssets } from '../lib/api.js';

function mergeAssetEntries(pages) {
  return pages
    .flatMap((page) => Array.isArray(page?.entries) ? page.entries : [])
    .sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')));
}

export function useAssets({ owner = '', initialType = 'all', initialQuery = '' }) {
  const [assetType, setAssetType] = useState(initialType);
  const [query, setQuery] = useState(initialQuery);
  const [entries, setEntries] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function loadInitial() {
      setLoading(true);
      setError('');
      try {
        const types = assetType === 'all' ? ['component', 'variant'] : [assetType];
        const pages = await Promise.all(types.map((type) => listAssets(type, {
          owner,
          q: query,
        })));
        if (cancelled) {
          return;
        }
        setEntries(mergeAssetEntries(pages));
        if (types.length === 1) {
          setNextCursor(pages[0]?.nextCursor || null);
        } else {
          setNextCursor(null);
        }
      } catch (errorValue) {
        if (cancelled) {
          return;
        }
        setEntries([]);
        setNextCursor(null);
        setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadInitial();
    return () => {
      cancelled = true;
    };
  }, [assetType, owner, query]);

  async function loadMore() {
    if (!nextCursor || assetType === 'all') {
      return;
    }
    setLoadingMore(true);
    setError('');
    try {
      const page = await listAssets(assetType, {
        owner,
        q: query,
        cursor: nextCursor,
      });
      setEntries((previous) => previous.concat(Array.isArray(page?.entries) ? page.entries : []));
      setNextCursor(page?.nextCursor || null);
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setLoadingMore(false);
    }
  }

  return {
    assetType,
    entries,
    error,
    hasMore: Boolean(nextCursor) && assetType !== 'all',
    loading,
    loadingMore,
    loadMore,
    query,
    refreshKey: `${owner}:${assetType}:${query}`,
    setAssetType,
    setEntries,
    setQuery,
  };
}
