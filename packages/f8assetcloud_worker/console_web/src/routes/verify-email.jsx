import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { apiFetch } from '../lib/api.js';

export function VerifyEmailRoute() {
  const [searchParams] = useSearchParams();
  const initialToken = String(searchParams.get('token') || '');
  const [token, setToken] = useState(initialToken);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState(() => searchParams.get('verified') ? 'Email verified. You can continue in the portal.' : '');
  const autoStartedRef = useRef(false);

  useEffect(() => {
    if (!initialToken || autoStartedRef.current) {
      return;
    }
    autoStartedRef.current = true;
    void handleVerify(initialToken);
  }, [initialToken]);

  async function handleVerify(value = token) {
    setPending(true);
    setMessage('');
    try {
      await apiFetch(`/v1/auth/verify-email?token=${encodeURIComponent(String(value).trim())}`);
      setMessage('Email verified. You can continue in the portal.');
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex w-full items-center justify-center">
      <div className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/45 p-6 shadow-[0_30px_90px_rgba(0,0,0,0.32)] backdrop-blur-xl">
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Verification</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Verify your email</h2>
        <form className="mt-8 space-y-4" onSubmit={(event) => {
          event.preventDefault();
          void handleVerify();
        }}>
          <label className="block text-sm text-slate-300">
            Verification Token
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={token} onChange={(event) => setToken(event.target.value)} />
          </label>
          {message ? <p className="text-sm text-slate-200">{message}</p> : null}
          <Button type="submit" disabled={pending || !token.trim()}>
            {pending ? 'Verifying...' : 'Verify Email'}
          </Button>
        </form>
        <div className="mt-8 flex flex-wrap gap-4 text-sm text-slate-300">
          <Link className="text-cyan-200 hover:text-white" to="/login">Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
