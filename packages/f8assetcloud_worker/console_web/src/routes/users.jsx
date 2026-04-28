import { useEffect, useState } from 'react';

import { Button } from '../components/ui/button.jsx';
import { useSession } from '../hooks/useSession.jsx';
import { createManagedUser, deleteManagedUser, listManagedUsers, updateManagedUser } from '../lib/api.js';

export function UsersRoute() {
  const { currentUser } = useSession();
  const [users, setUsers] = useState([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [newUser, setNewUser] = useState({
    name: '',
    email: '',
    password: '',
    role: 'user',
  });

  async function loadUsers() {
    setLoading(true);
    setMessage('');
    try {
      const payload = await listManagedUsers({ q: query });
      setUsers(Array.isArray(payload?.entries) ? payload.entries : []);
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadUsers();
  }, [query]);

  async function handleCreate(event) {
    event.preventDefault();
    setMessage('');
    try {
      await createManagedUser(newUser);
      setNewUser({ name: '', email: '', password: '', role: 'user' });
      await loadUsers();
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    }
  }

  async function handleToggleAdmin(user) {
    setMessage('');
    try {
      await updateManagedUser(user.userId, {
        name: user.name,
        role: user.role === 'admin' ? 'user' : 'admin',
      });
      await loadUsers();
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    }
  }

  async function handleDelete(user) {
    setMessage('');
    try {
      await deleteManagedUser(user.userId);
      await loadUsers();
    } catch (errorValue) {
      setMessage(errorValue instanceof Error ? errorValue.message : String(errorValue));
    }
  }

  return (
    <section className="space-y-6">
      <header>
        <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Admin</p>
        <h2 className="mt-3 text-3xl font-semibold text-white">Users</h2>
      </header>
      <div className="rounded-[2rem] border border-white/10 bg-white/5 p-6">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
          <input className="rounded-2xl border border-white/12 bg-slate-950/55 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search users" />
          <Button variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => void loadUsers()} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh'}
          </Button>
        </div>
        {message ? <p className="mt-4 text-sm text-slate-200">{message}</p> : null}
        <div className="mt-6 overflow-x-auto">
          <table className="min-w-full text-left text-sm text-slate-200">
            <thead>
              <tr className="border-b border-white/10 text-slate-400">
                <th className="py-3 pr-4">Name</th>
                <th className="py-3 pr-4">Email</th>
                <th className="py-3 pr-4">Role</th>
                <th className="py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const isSelf = user.userId === currentUser?.userId;
                return (
                  <tr key={user.userId} className="border-b border-white/6">
                    <td className="py-4 pr-4">{user.name}</td>
                    <td className="py-4 pr-4">{user.email}</td>
                    <td className="py-4 pr-4">{user.role}</td>
                    <td className="py-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          className="border-white/15 bg-white/5 text-white hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                          onClick={() => void handleToggleAdmin(user)}
                          disabled={isSelf}
                        >
                          {user.role === 'admin' ? 'Make User' : 'Make Admin'}
                        </Button>
                        <Button type="button" variant="destructive" onClick={() => void handleDelete(user)} disabled={isSelf}>
                          Delete
                        </Button>
                        {isSelf ? <span className="text-xs text-slate-400">Current account</span> : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <form className="grid gap-4 rounded-[2rem] border border-white/10 bg-white/5 p-6 md:grid-cols-2" onSubmit={(event) => void handleCreate(event)}>
        <input className="rounded-2xl border border-white/12 bg-slate-950/55 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" placeholder="Name" value={newUser.name} onChange={(event) => setNewUser((previous) => ({ ...previous, name: event.target.value }))} />
        <input className="rounded-2xl border border-white/12 bg-slate-950/55 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" placeholder="Email" value={newUser.email} onChange={(event) => setNewUser((previous) => ({ ...previous, email: event.target.value }))} />
        <input className="rounded-2xl border border-white/12 bg-slate-950/55 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" placeholder="Password" type="password" value={newUser.password} onChange={(event) => setNewUser((previous) => ({ ...previous, password: event.target.value }))} />
        <select className="rounded-2xl border border-white/12 bg-slate-950/55 px-4 py-3 text-white focus:border-cyan-300/40 focus:outline-none" value={newUser.role} onChange={(event) => setNewUser((previous) => ({ ...previous, role: event.target.value }))}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
          <option value="readonly">Read Only</option>
        </select>
        <div className="md:col-span-2">
          <Button type="submit" disabled={!newUser.name.trim() || !newUser.email.trim() || !newUser.password}>
            Create User
          </Button>
        </div>
      </form>
    </section>
  );
}
