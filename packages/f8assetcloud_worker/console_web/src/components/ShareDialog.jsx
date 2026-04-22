import { useEffect, useRef, useState } from 'react';
import { Check, Link2 } from 'lucide-react';

import { Button } from './ui/button.jsx';
import { copyToClipboard } from '../lib/clipboard.js';

export function ShareDialog({ assetId }) {
  const [status, setStatus] = useState('idle');
  const resetTimerRef = useRef(0);
  const shareUrl = `${window.location.origin}/assets/${encodeURIComponent(assetId)}`;

  useEffect(() => {
    return () => {
      window.clearTimeout(resetTimerRef.current);
    };
  }, []);

  function scheduleReset() {
    window.clearTimeout(resetTimerRef.current);
    resetTimerRef.current = window.setTimeout(() => {
      setStatus('idle');
    }, 1800);
  }

  async function handleCopy() {
    setStatus('pending');
    try {
      await copyToClipboard(shareUrl);
      setStatus('copied');
    } catch (error) {
      console.error('ShareDialog: failed to copy share URL', error);
      setStatus('error');
    }
    scheduleReset();
  }

  const copied = status === 'copied';
  const failed = status === 'error';

  return (
    <>
      <Button
        type="button"
        variant="outline"
        className="border-white/15 bg-white/5 text-white hover:bg-white/10"
        onClick={() => void handleCopy()}
        disabled={status === 'pending'}
      >
        {copied ? <Check className="size-4" /> : <Link2 className="size-4" />}
        {status === 'pending' ? 'Copying...' : copied ? 'Link Copied' : failed ? 'Copy Failed' : 'Share'}
      </Button>
      <span className="sr-only" aria-live="polite">
        {copied ? 'Share link copied to clipboard.' : failed ? 'Copying the share link failed.' : ''}
      </span>
    </>
  );
}
