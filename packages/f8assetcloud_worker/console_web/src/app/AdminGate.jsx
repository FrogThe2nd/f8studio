import { Navigate, Outlet } from 'react-router-dom';

import { useSession } from '../hooks/useSession.jsx';

export function AdminGate() {
  const { authResolved, currentUser } = useSession();
  if (!authResolved) {
    return null;
  }
  if (!currentUser?.isAdmin) {
    return <Navigate replace to="/assets/mine" />;
  }
  return <Outlet />;
}
