import { useEffect, useState } from 'react';

import { getAssetDetail, getAssetVersionContent, listAssetVersions, resolveAsset } from '../lib/api.js';

export function useAsset(assetId) {
  const [assetType, setAssetType] = useState('');
  const [asset, setAsset] = useState(null);
  const [versions, setVersions] = useState([]);
  const [versionContentByNumber, setVersionContentByNumber] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!assetId) {
      setAssetType('');
      setAsset(null);
      setVersions([]);
      setVersionContentByNumber({});
      setError('');
      return undefined;
    }
    setLoading(true);
    setError('');
    setVersionContentByNumber({});
    void (async () => {
      try {
        const resolved = await resolveAsset(assetId);
        const resolvedType = String(resolved?.assetType || '');
        const [detail, versionPage] = await Promise.all([
          getAssetDetail(resolvedType, assetId),
          listAssetVersions(resolvedType, assetId),
        ]);
        if (cancelled) {
          return;
        }
        setAssetType(resolvedType);
        setAsset(detail);
        setVersions(Array.isArray(versionPage?.versions) ? versionPage.versions : []);
      } catch (errorValue) {
        if (cancelled) {
          return;
        }
        setAssetType('');
        setAsset(null);
        setVersions([]);
        setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [assetId]);

  async function loadVersionContent(versionNumber) {
    const key = String(versionNumber);
    if (!assetType || !assetId) {
      return null;
    }
    if (Object.prototype.hasOwnProperty.call(versionContentByNumber, key)) {
      return versionContentByNumber[key];
    }
    const payload = await getAssetVersionContent(assetType, assetId, versionNumber);
    setVersionContentByNumber((previous) => ({
      ...previous,
      [key]: payload,
    }));
    return payload;
  }

  return {
    asset,
    assetType,
    error,
    loadVersionContent,
    loading,
    setAsset,
    versionContentByNumber,
    versions,
  };
}
