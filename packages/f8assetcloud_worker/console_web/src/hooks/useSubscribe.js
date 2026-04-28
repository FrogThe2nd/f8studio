import { useState } from 'react';

import { subscribeAsset, unsubscribeAsset } from '../lib/api.js';

export function useSubscribe({ assetType, assetId, initialSubscribed, onSettled }) {
  const [subscribed, setSubscribed] = useState(Boolean(initialSubscribed));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  async function toggleSubscription() {
    const previousSubscribed = subscribed;
    const nextSubscribed = !previousSubscribed;
    setPending(true);
    setError('');
    setSubscribed(nextSubscribed);
    try {
      const payload = nextSubscribed
        ? await subscribeAsset(assetType, assetId)
        : await unsubscribeAsset(assetType, assetId);
      setSubscribed(Boolean(payload?.subscribed));
      if (onSettled) {
        onSettled(payload);
      }
      return payload;
    } catch (errorValue) {
      setSubscribed(previousSubscribed);
      setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
      throw errorValue;
    } finally {
      setPending(false);
    }
  }

  return {
    error,
    pending,
    setSubscribed,
    subscribed,
    toggleSubscription,
  };
}
