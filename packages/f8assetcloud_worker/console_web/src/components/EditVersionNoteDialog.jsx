import { Suspense, lazy, useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { FilePenLine, X } from 'lucide-react';

import { updateAssetVersionNote } from '../lib/api.js';
import { Button } from './ui/button.jsx';

const MarkdownEditor = lazy(async () => {
  const module = await import('./MarkdownEditor.jsx');
  return {
    default: module.MarkdownEditor,
  };
});

function EditorLoader() {
  return <p className="text-sm text-slate-400">Loading editor...</p>;
}

export function EditVersionNoteDialog({
  assetId,
  assetType,
  version,
  onUpdated,
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setNote(String(version?.changeSummary || ''));
  }, [version?.changeSummary, version?.versionNumber]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!version) {
      return;
    }
    setPending(true);
    setError('');
    try {
      const payload = await updateAssetVersionNote(assetType, assetId, version.versionNumber, {
        changeSummary: note.trim() ? note.trim() : null,
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
        <Button type="button" variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
          <FilePenLine className="size-4" />
          Edit Notes
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[88vh] w-[min(92vw,860px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[1.75rem] border border-white/10 bg-slate-950 p-6 shadow-[0_30px_80px_rgba(0,0,0,0.42)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-xl font-semibold text-white">Edit version notes</Dialog.Title>
              <Dialog.Description className="mt-2 text-sm text-slate-300">
                Update the notes for version {Number(version?.versionNumber)} without changing the version payload.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" className="rounded-full border border-white/10 p-2 text-slate-300 hover:text-white">
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>
          <form className="mt-6 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
            <Suspense fallback={<EditorLoader />}>
              <MarkdownEditor
                value={note}
                onChange={setNote}
                label="Version notes"
                placeholder="What changed in this version?"
                minHeightClassName="min-h-52"
              />
            </Suspense>
            {error ? <p className="text-sm text-rose-200">{error}</p> : null}
            <div className="flex justify-end gap-3">
              <Button type="button" variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={pending}>
                {pending ? 'Saving...' : 'Save Notes'}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
