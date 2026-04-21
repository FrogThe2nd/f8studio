import { AssetCard } from './AssetCard.jsx';

export function AssetGrid({ assets }) {
  return (
    <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
      {(assets || []).map((asset) => {
        const key = String(asset?.assetId || asset?.componentId || asset?.variantId || crypto.randomUUID());
        return <AssetCard key={key} asset={asset} />;
      })}
    </div>
  );
}
