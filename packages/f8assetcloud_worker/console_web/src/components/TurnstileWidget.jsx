import { useEffect, useRef, useState } from 'react';

const TURNSTILE_SCRIPT_ID = 'cf-turnstile-script';
const TURNSTILE_SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

let turnstileScriptPromise = null;

function loadTurnstileScript() {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('Turnstile is unavailable outside the browser.'));
  }
  if (window.turnstile?.render) {
    return Promise.resolve(window.turnstile);
  }
  if (turnstileScriptPromise) {
    return turnstileScriptPromise;
  }
  turnstileScriptPromise = new Promise((resolve, reject) => {
    const existingScript = document.getElementById(TURNSTILE_SCRIPT_ID);
    if (existingScript instanceof HTMLScriptElement) {
      existingScript.addEventListener('load', () => resolve(window.turnstile), { once: true });
      existingScript.addEventListener('error', () => reject(new Error('Failed to load Turnstile.')), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.id = TURNSTILE_SCRIPT_ID;
    script.src = TURNSTILE_SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve(window.turnstile);
    script.onerror = () => reject(new Error('Failed to load Turnstile.'));
    document.head.appendChild(script);
  });
  return turnstileScriptPromise;
}

export function TurnstileWidget({ siteKey, onTokenChange, resetKey = 0 }) {
  const containerRef = useRef(null);
  const widgetIdRef = useRef(null);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    onTokenChange('');
    setLoadError('');
    if (!siteKey) {
      return undefined;
    }
    let cancelled = false;

    void loadTurnstileScript()
      .then((turnstile) => {
        if (cancelled || !containerRef.current || !turnstile?.render) {
          return;
        }
        widgetIdRef.current = turnstile.render(containerRef.current, {
          sitekey: siteKey,
          callback: (token) => {
            onTokenChange(String(token || ''));
          },
          'expired-callback': () => {
            onTokenChange('');
          },
          'error-callback': () => {
            onTokenChange('');
          },
        });
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setLoadError(error instanceof Error ? error.message : String(error));
        onTokenChange('');
      });

    return () => {
      cancelled = true;
      if (widgetIdRef.current !== null && window.turnstile?.remove) {
        window.turnstile.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
    };
  }, [onTokenChange, resetKey, siteKey]);

  if (!siteKey) {
    return null;
  }

  return (
    <div className="space-y-2">
      <div ref={containerRef} data-testid="turnstile-widget" />
      {loadError ? <p className="text-sm text-rose-200">{loadError}</p> : null}
    </div>
  );
}
