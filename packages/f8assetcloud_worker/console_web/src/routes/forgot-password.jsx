import { useState } from 'react';
import { Link } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { TurnstileWidget } from '../components/TurnstileWidget.jsx';
import { requestPasswordResetEmail } from '../lib/api.js';
import { useSession } from '../hooks/useSession.jsx';

export function ForgotPasswordRoute() {
  const { authProviders } = useSession();
  const [email, setEmail] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [captchaResponse, setCaptchaResponse] = useState('');
  const [captchaResetKey, setCaptchaResetKey] = useState(0);
  const turnstileSiteKey = String(authProviders?.turnstileSiteKey || '').trim();
  const captchaRequired = Boolean(turnstileSiteKey);

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setMessage('');
    try {
      await requestPasswordResetEmail({
        email: email.trim(),
        redirectTo: `${window.location.origin}/reset-password`,
      }, {
        captchaResponse,
      });
      setMessage('If that account exists, a reset link is on the way.');
      setEmail('');
      setCaptchaResponse('');
      setCaptchaResetKey((current) => current + 1);
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
      if (captchaRequired) {
        setCaptchaResponse('');
        setCaptchaResetKey((current) => current + 1);
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex w-full items-center justify-center">
      <div className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/45 p-6 shadow-[0_30px_90px_rgba(0,0,0,0.32)] backdrop-blur-xl">
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Recovery</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Reset your password</h2>
        <p className="mt-3 text-sm leading-6 text-slate-300">
          Enter your account email and we’ll send you a secure reset link.
        </p>
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSubmit(event)}>
          <label className="block text-sm text-slate-300">
            Email
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          {captchaRequired ? (
            <div className="space-y-2">
              <p className="text-sm text-slate-300">Complete the security check to request a reset link.</p>
              <TurnstileWidget
                siteKey={turnstileSiteKey}
                onTokenChange={setCaptchaResponse}
                resetKey={captchaResetKey}
              />
            </div>
          ) : null}
          {message ? <p className="text-sm text-slate-200">{message}</p> : null}
          <Button type="submit" disabled={pending || !email.trim() || (captchaRequired && !captchaResponse)}>
            {pending ? 'Sending...' : 'Send Reset Link'}
          </Button>
        </form>
        <div className="mt-8 flex flex-wrap gap-4 text-sm text-slate-300">
          <Link className="text-cyan-200 hover:text-white" to="/login">Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
