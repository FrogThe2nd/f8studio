import { Link, Outlet } from 'react-router-dom';

import { Button } from '../components/ui/button.jsx';
import { useSession } from '../hooks/useSession.jsx';

export function PublicLayout({ title = 'AssetCloud Portal', subtitle = 'A calmer way to browse and share studio assets.', children }) {
  const { isAuthenticated } = useSession();

  return (
    <div className="min-h-screen">
      <div className="mx-auto flex min-h-screen max-w-[1240px] flex-col px-4 py-5 sm:px-6">
        <header className="flex flex-wrap items-center justify-between gap-4 rounded-[2rem] border border-white/10 bg-slate-950/50 px-5 py-4 backdrop-blur-xl">
          <div>
            <p className="text-xs uppercase tracking-[0.34em] text-cyan-200/70">Feel8 Studio</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">{title}</h1>
            <p className="mt-1 text-sm text-slate-300">{subtitle}</p>
          </div>
          <div className="flex flex-wrap gap-3">
            {isAuthenticated ? (
              <Button asChild variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
                <Link to="/assets/mine">Open Portal</Link>
              </Button>
            ) : (
              <>
                <Button asChild variant="outline" className="border-white/15 bg-white/5 text-white hover:bg-white/10">
                  <Link to="/browse">Browse</Link>
                </Button>
                <Button asChild>
                  <Link to="/login">Sign In</Link>
                </Button>
              </>
            )}
          </div>
        </header>
        <main className="flex flex-1 items-stretch py-8">
          {children || <Outlet />}
        </main>
      </div>
    </div>
  );
}
