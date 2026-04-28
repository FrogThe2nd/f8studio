import { FolderKanban, LayoutGrid, Settings, Shield, UserRound } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { cn } from '../lib/cn.js';
import { useSession } from '../hooks/useSession.jsx';

const primaryNav = [
  { to: '/assets/mine', label: 'My Assets', icon: FolderKanban },
  { to: '/browse', label: 'Browse', icon: LayoutGrid },
  { to: '/profile', label: 'Profile', icon: UserRound },
];

const adminNav = [
  { to: '/admin/users', label: 'Users', icon: Shield },
  { to: '/admin/settings', label: 'Settings', icon: Settings },
];

export function Layout({ children = null }) {
  const { currentUser, signOut } = useSession();

  return (
    <ShellFrame
      title="AssetCloud Portal"
      subtitle="Browse, share, download, and manage your studio assets."
      sidebar={
        <>
          <div className="rounded-3xl border border-white/10 bg-white/5 p-4 shadow-[0_18px_50px_rgba(0,0,0,0.24)]">
            <p className="text-xs uppercase tracking-[0.32em] text-cyan-200/75">Signed In</p>
            <h2 className="mt-2 text-lg font-semibold text-white">{currentUser?.name || 'Account'}</h2>
            <p className="mt-1 text-sm text-slate-300">{currentUser?.email || ''}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <span className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-3 py-1 text-xs font-medium text-cyan-100">
                {currentUser?.role || 'user'}
              </span>
              {currentUser?.emailVerified ? (
                <span className="rounded-full border border-emerald-300/30 bg-emerald-300/10 px-3 py-1 text-xs font-medium text-emerald-100">
                  Email verified
                </span>
              ) : null}
            </div>
          </div>
          <nav className="space-y-2">
            {primaryNav.map((item) => (
              <NavItem key={item.to} item={item} />
            ))}
          </nav>
          {currentUser?.isAdmin ? (
            <div className="space-y-2">
              <p className="px-3 text-[11px] uppercase tracking-[0.3em] text-slate-400">Admin</p>
              {adminNav.map((item) => (
                <NavItem key={item.to} item={item} />
              ))}
            </div>
          ) : null}
          <Button variant="outline" className="justify-start border-white/15 bg-white/5 text-white hover:bg-white/10" onClick={() => void signOut()}>
            Sign Out
          </Button>
        </>
      }
    >
      {children || <Outlet />}
    </ShellFrame>
  );
}

function NavItem({ item }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      className={({ isActive }) => cn(
        'flex items-center gap-3 rounded-2xl border px-3 py-3 text-sm font-medium transition',
        isActive
          ? 'border-cyan-300/40 bg-cyan-300/15 text-white shadow-[0_14px_40px_rgba(18,122,145,0.22)]'
          : 'border-white/8 bg-white/4 text-slate-300 hover:border-white/18 hover:bg-white/8 hover:text-white',
      )}
    >
      <Icon className="size-4" />
      <span>{item.label}</span>
    </NavLink>
  );
}

export function ShellFrame({ children, sidebar, title, subtitle }) {
  return (
    <div className="min-h-screen">
      <div className="mx-auto grid min-h-screen max-w-[1480px] gap-6 px-4 py-5 lg:grid-cols-[280px_minmax(0,1fr)] lg:px-6">
        <aside className="flex flex-col gap-4 lg:sticky lg:top-0 lg:h-screen lg:py-3">
          <div className="rounded-[2rem] border border-white/10 bg-slate-950/55 p-5 backdrop-blur-xl">
            <p className="text-xs uppercase tracking-[0.36em] text-cyan-200/70">Feel8 Studio</p>
            <h1 className="mt-3 text-2xl font-semibold text-white">{title}</h1>
            <p className="mt-2 text-sm leading-6 text-slate-300">{subtitle}</p>
          </div>
          <div className="flex flex-1 flex-col gap-4 rounded-[2rem] border border-white/10 bg-slate-950/45 p-4 backdrop-blur-xl">
            {sidebar}
          </div>
        </aside>
        <main className="min-w-0 rounded-[2rem] border border-white/10 bg-slate-950/45 p-4 backdrop-blur-xl sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
