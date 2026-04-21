import { Download } from 'lucide-react';

import { Button } from './ui/button.jsx';
import { buildAssetDownloadPath } from '../lib/api.js';

export function DownloadButton({ assetType, assetId, versionNumber = null, children = 'Download' }) {
  const href = buildAssetDownloadPath(assetType, assetId, versionNumber);
  return (
    <Button asChild variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
      <a href={href}>
        <Download className="size-4" />
        {children}
      </a>
    </Button>
  );
}
