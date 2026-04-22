import { Suspense, lazy, useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Pencil, X } from 'lucide-react';

import { Button } from './ui/button.jsx';
import { updateAssetMeta } from '../lib/api.js';

const MarkdownEditor = lazy(async () => {
  const module = await import('./MarkdownEditor.jsx');
  return {
    default: module.MarkdownEditor,
  };
});

function EditorLoader() {
  return <p className="text-sm text-slate-400">Loading editor...</p>;
}

export function EditAssetDialog({ asset, assetType, onUpdated }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [tags, setTags] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setName(String(asset?.name || ''));
    setDescription(String(asset?.description || ''));
    setTags(Array.isArray(asset?.tags) ? asset.tags.join(', ') : '');
  }, [asset?.description, asset?.name, asset?.tags]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!asset) {
      return;
    }
    setPending(true);
    setError('');
    try {
      const assetId = String(asset.assetId || asset.componentId || asset.variantId);
      const payload = await updateAssetMeta(assetType, assetId, {
        name,
        description,
        tags: tags.split(',').map((entry) => entry.trim()).filter(Boolean),
      });
      if (onUpdated) {
        onUpdated(payload);
      }
      setOpen(false);
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
          <Pencil className="size-4" />
          Edit
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[88vh] w-[min(92vw,980px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[1.75rem] border border-white/10 bg-slate-950 p-6 shadow-[0_30px_80px_rgba(0,0,0,0.42)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-xl font-semibold text-white">Edit asset details</Dialog.Title>
              <Dialog.Description className="mt-2 text-sm text-slate-300">
                Update the head metadata without publishing a new version.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="rounded-full border border-white/10 p-2 text-slate-300 hover:text-white">
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>
          <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
            <label className="block text-sm text-slate-300">
              Name
              <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <Suspense fallback={<EditorLoader />}>
              <MarkdownEditor value={description} onChange={setDescription} />
            </Suspense>
            <label className="block text-sm text-slate-300">
              Tags
              <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="comma, separated, tags" />
            </label>
            {error ? <p className="text-sm text-rose-200">{error}</p> : null}
            <div className="flex justify-end gap-3">
              <Button type="button" variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? 'Saving...' : 'Save'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
