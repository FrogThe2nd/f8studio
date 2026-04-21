import { useState } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { authClient } from '../authClient.js';
import { useSession } from '../hooks/useSession.jsx';

export function LoginRoute() {
  const location = useLocation();
  const { authProviders, authResolved, isAuthenticated, siteSettings } = useSession();
  const redirectTarget = String(location.state?.from || '/assets/mine');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  if (authResolved && isAuthenticated) {
    return <Navigate replace to={redirectTarget} />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setError('');
    try {
      await authClient.signIn.email({
        email: email.trim(),
        password,
      });
      window.location.assign(redirectTarget);
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setPending(false);
    }
  }

  async function handleGoogle() {
    setPending(true);
    setError('');
    try {
      await authClient.signIn.social({
        provider: 'google',
        callbackURL: redirectTarget,
      });
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : String(errorValue));
      setPending(false);
    }
  }

  return (
    <div className="flex w-full items-center justify-center">
      <div className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/45 p-6 shadow-[0_30px_90px_rgba(0,0,0,0.32)] backdrop-blur-xl">
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Portal Access</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Sign in to AssetCloud</h2>
        <p className="mt-3 text-sm leading-6 text-slate-300">
          Open your assets, manage subscriptions, and share public links from one place.
        </p>
        {!siteSettings.allowUserRegistration ? (
          <p className="mt-3 text-sm leading-6 text-slate-300">
            Public registration is currently disabled. Sign in with an existing email/password account.
          </p>
        ) : null}
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <label className="block text-sm text-slate-300">
            Email
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
          </label>
          <label className="block text-sm text-slate-300">
            Password
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" />
          </label>
          {error ? <p className="text-sm text-rose-200">{error}</p> : null}
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={pending || !email.trim() || !password}>
              {pending ? 'Signing in...' : 'Sign In'}
            </Button>
            {authProviders.google && siteSettings.allowUserRegistration ? (
              <Button type="button" variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => void handleGoogle()} disabled={pending}>
                Continue with Google
              </Button>
            ) : null}
          </div>
        </form>
        <div className="mt-8 flex flex-wrap gap-4 text-sm text-slate-300">
          <Link className="text-cyan-200 hover:text-white" to="/forgot-password">Forgot password?</Link>
          <Link className="text-cyan-200 hover:text-white" to="/verify-email">Verify email</Link>
          {siteSettings.allowUserRegistration ? (
            <Link className="text-cyan-200 hover:text-white" to="/register">Create account</Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}
