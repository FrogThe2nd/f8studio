import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { mockSubscribeAsset, mockUnsubscribeAsset } = vi.hoisted(() => ({
  mockSubscribeAsset: vi.fn(),
  mockUnsubscribeAsset: vi.fn(),
}));

vi.mock('../lib/api.js', () => ({
  subscribeAsset: (...args) => mockSubscribeAsset(...args),
  unsubscribeAsset: (...args) => mockUnsubscribeAsset(...args),
}));

import { useSubscribe } from './useSubscribe.js';

function createDeferred() {
  let resolvePromise;
  let rejectPromise;
  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return {
    promise,
    resolve: resolvePromise,
    reject: rejectPromise,
  };
}

describe('useSubscribe', () => {
  beforeEach(() => {
    mockSubscribeAsset.mockReset();
    mockUnsubscribeAsset.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('optimistically flips subscribed state and rolls back on error', async () => {
    const deferred = createDeferred();
    mockSubscribeAsset.mockReturnValue(deferred.promise);

    const onSettled = vi.fn();
    const { result } = renderHook(() => useSubscribe({
      assetType: 'variant',
      assetId: 'variant-1',
      initialSubscribed: false,
      onSettled,
    }));

    let togglePromise;
    act(() => {
      togglePromise = result.current.toggleSubscription();
    });

    expect(result.current.subscribed).toBe(true);
    expect(result.current.pending).toBe(true);
    expect(result.current.error).toBe('');

    await act(async () => {
      deferred.reject(new Error('Subscription failed'));
      try {
        await togglePromise;
      } catch {
        // The hook should surface the original failure to callers.
      }
    });

    expect(result.current.subscribed).toBe(false);
    expect(result.current.pending).toBe(false);
    expect(result.current.error).toBe('Subscription failed');
    expect(onSettled).not.toHaveBeenCalled();
    expect(mockSubscribeAsset).toHaveBeenCalledWith('variant', 'variant-1');
  });
});
