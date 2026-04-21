import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Link2, X } from 'lucide-react';

import { Button } from './ui/button.jsx';
import { copyToClipboard } from '../lib/clipboard.js';

export function ShareDialog({ assetId }) {
  const [copied, setCopied] = useState(false);
  const shareUrl = `${window.location.origin}/assets/${encodeURIComponent(assetId)}`;

  async function handleCopy() {
    await copyToClipboard(shareUrl);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <Button variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
          <Link2 className="size-4" />
          Share
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 rounded-[1.75rem] border border-white/10 bg-slate-950 p-6 shadow-[0_30px_80px_rgba(0,0,0,0.42)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-xl font-semibold text-white">Share this asset</Dialog.Title>
              <Dialog.Description className="mt-2 text-sm text-slate-300">
                Anyone with the link can view public assets. Private assets still return not found.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="rounded-full border border-white/10 p-2 text-slate-300 hover:text-white">
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>
          <div className="mt-6 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-cyan-100">
            {shareUrl}
          </div>
          <div className="mt-6 flex justify-end gap-3">
            <Button variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => void handleCopy()}>
              {copied ? 'Copied' : 'Copy Link'}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
