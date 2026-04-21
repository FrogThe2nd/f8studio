import { Bell } from 'lucide-react';

import { Button } from './ui/button.jsx';
import { useSubscribe } from '../hooks/useSubscribe.js';

export function SubscribeButton({ assetType, assetId, subscribed: initialSubscribed, onSettled }) {
  const { error, pending, subscribed, toggleSubscription } = useSubscribe({
    assetType,
    assetId,
    initialSubscribed,
    onSettled,
  });

  return (
    <div className="space-y-2">
      <Button onClick={() => void toggleSubscription()} disabled={pending}>
        <Bell className="size-4" />
        {pending ? 'Saving...' : subscribed ? 'Unsubscribe' : 'Subscribe'}
      </Button>
      {error ? <p className="text-sm text-rose-200">{error}</p> : null}
    </div>
  );
}
