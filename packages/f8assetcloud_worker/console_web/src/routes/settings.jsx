import { useEffect, useState } from 'react';

import { Button } from '../components/ui/button.jsx';
import { getManagedSiteSettings, updateManagedSiteSettings } from '../lib/api.js';

export function SettingsRoute() {
  const [allowUserRegistration, setAllowUserRegistration] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const payload = await getManagedSiteSettings();
        if (!cancelled) {
          setAllowUserRegistration(Boolean(payload?.allowUserRegistration));
        }
      } catch (errorValue) {
        if (!cancelled) {
          setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSave() {
    setLoading(true);
    setMessage('');
    try {
      const payload = await updateManagedSiteSettings({ allowUserRegistration });
      setAllowUserRegistration(Boolean(payload?.allowUserRegistration));
      setMessage('Settings updated.');
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Admin</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Settings</h2>
      </header>
      <div className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
        <label className="flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-slate-950/55 p-4 text-sm text-slate-200">
          <span>
            <strong className="block text-white">Allow user registration</strong>
            <span className="mt-1 block text-slate-400">Controls whether new public accounts can sign up.</span>
          </span>
          <input type="checkbox" checked={allowUserRegistration} onChange={(event) => setAllowUserRegistration(event.target.checked)} />
        </label>
        {message ? <p className="mt-4 text-sm text-slate-200">{message}</p> : null}
        <div className="mt-6">
          <Button type="button" disabled={loading} onClick={() => void handleSave()}>
            {loading ? 'Saving...' : 'Save Settings'}
          </Button>
        </div>
      </div>
    </section>
  );
}
