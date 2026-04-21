import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { requestPasswordReset } from '../lib/api.js';

export function ResetPasswordRoute() {
  const [searchParams] = useSearchParams();
  const [token, setToken] = useState(() => searchParams.get('token') || '');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  async function handleSubmit(event) {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setMessage('Passwords do not match.');
      return;
    }
    setPending(true);
    setMessage('');
    try {
      await requestPasswordReset({
        token: token.trim(),
        newPassword,
      });
      setMessage('Password updated. You can sign in now.');
      setNewPassword('');
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
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Recovery</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Choose a new password</h2>
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <label className="block text-sm text-slate-300">
            Reset Token
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={token} onChange={(event) => setToken(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            New Password
            <input type="password" className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            Confirm Password
            <input type="password" className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
          </label>
          {message ? <p className="text-sm text-slate-200">{message}</p> : null}
          <Button type="submit" disabled={pending || !token.trim() || !newPassword || !confirmPassword}>
            {pending ? 'Resetting...' : 'Reset Password'}
          </Button>
        </form>
        <div className="mt-8 flex flex-wrap gap-4 text-sm text-slate-300">
          <Link className="text-cyan-200 hover:text-white" to="/login">Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
