import { useState } from 'react';

import { Button } from '../components/ui/button.jsx';
import { useSession } from '../hooks/useSession.jsx';
import { updateCurrentUser } from '../lib/api.js';

export function ProfileRoute() {
  const { currentUser, linkedAccounts, refreshProfile } = useSession();
  const [name, setName] = useState(() => String(currentUser?.name || ''));
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  async function handleSave(event) {
    event.preventDefault();
    setPending(true);
    setMessage('');
    try {
      await updateCurrentUser({ name: name.trim() });
      await refreshProfile();
      setMessage('Profile updated.');
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Account</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Profile</h2>
        <form className="mt-8 space-y-4" onSubmit={(event) => void handleSave(event)}>
          <label className="block text-sm text-slate-300">
            Display name
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="block text-sm text-slate-300">
            Email
            <input className="mt-2 w-full rounded-2xl border border-white/12 bg-white/5 px-4 py-3 text-slate-400" value={String(currentUser?.email || '')} disabled />
          </label>
          {message ? <p className="text-sm text-slate-200">{message}</p> : null}
          <Button type="submit" disabled={pending || !name.trim()}>
            {pending ? 'Saving...' : 'Save Profile'}
          </Button>
        </form>
      </section>
      <aside className="space-y-6">
        <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
          <h3 className="text-lg font-semibold text-white">Status</h3>
          <dl className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="flex justify-between gap-4">
              <dt>Role</dt>
              <dd>{currentUser?.role || 'user'}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>Email verified</dt>
              <dd>{currentUser?.emailVerified ? 'Yes' : 'No'}</dd>
            </div>
          </dl>
        </section>
        <section className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
          <h3 className="text-lg font-semibold text-white">Linked Accounts</h3>
          <ul className="mt-4 space-y-2 text-sm text-slate-300">
            {(linkedAccounts || []).length === 0 ? <li>No linked providers.</li> : null}
            {(linkedAccounts || []).map((account) => (
              <li key={`${account.providerId}:${account.accountId}`}>
                {account.providerId}: {account.accountId}
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </section>
  );
}
