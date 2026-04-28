import { Link } from 'react-router-dom';

import { PublicLayout } from '../app/PublicLayout.jsx';
import { Button } from '../components/ui/button.jsx';

export function NotFoundRoute() {
  return (
    <PublicLayout title="Page not found" subtitle="That route does not exist in the new portal yet.">
      <div className="flex w-full items-center justify-center">
        <div className="w-full max-w-xl rounded-[2rem] border border-white/10 bg-slate-950/45 p-6 text-center shadow-[0_30px_90px_rgba(0,0,0,0.32)] backdrop-blur-xl">
          <h2 className="text-3xl font-semibold text-white">Nothing here yet</h2>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            This route is not part of the root portal.
          </p>
          <div className="mt-8 flex justify-center gap-3">
            <Button asChild>
              <Link to="/browse">Browse assets</Link>
            </Button>
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
