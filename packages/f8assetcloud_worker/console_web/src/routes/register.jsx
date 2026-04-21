import { useState } from 'react';
import { Link, Navigate } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { authClient } from '../authClient.js';
import { useSession } from '../hooks/useSession.jsx';

export function RegisterRoute() {
  const { authResolved, isAuthenticated, siteSettings } = useSession();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  if (authResolved && isAuthenticated) {
    return <Navigate replace to="/assets/mine" />;
  }
  if (!siteSettings.allowUserRegistration) {
    return <Navigate replace to="/login" />;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (password !== confirmPassword) {
      setMessage('Passwords do not match.');
      return;
    }
    setPending(true);
    setMessage('');
    try {
      await authClient.signUp.email({
        name: name.trim(),
        email: email.trim(),
        password,
        callbackURL: `${window.location.origin}/verify-email?verified=1`,
      });
      setMessage('Account created. Verify your email, then sign in.');
      setName('');
      setEmail('');
      setPassword('');
      setConfirmPassword('');
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex w-full items-center justify-center">
      <div className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/45 p-6 shadow-[0_30px_90px_rgba(0,0,0,0.32)] backdrop-blur-xl">
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">New Account</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Create your portal account</h2>
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <label className="block text-sm text-slate-300">
            Name
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            Email
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            Password
            <input type="password" className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            Confirm Password
            <input type="password" className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
          </label>
          {message ? <p className="text-sm text-slate-200">{message}</p> : null}
          <Button type="submit" disabled={pending || !name.trim() || !email.trim() || !password || !confirmPassword}>
            {pending ? 'Creating account...' : 'Create Account'}
          </Button>
        </form>
        <div className="mt-8 flex flex-wrap gap-4 text-sm text-slate-300">
          <Link className="text-cyan-200 hover:text-white" to="/login">Back to sign in</Link>
          <Link className="text-cyan-200 hover:text-white" to="/verify-email">Verify email</Link>
        </div>
      </div>
    </div>
  );
}
