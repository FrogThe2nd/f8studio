import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { PublicLayout } from './PublicLayout.jsx';
import { useSession } from '../hooks/useSession.jsx';

export function AuthGate() {
  const location = useLocation();
  const { authResolved, isAuthenticated } = useSession();

  if (!authResolved) {
    return <PublicLayout title="Loading portal" subtitle="Restoring your AssetCloud session." />;
  }
  if (!isAuthenticated) {
    return <Navigate replace to="/login" state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}
