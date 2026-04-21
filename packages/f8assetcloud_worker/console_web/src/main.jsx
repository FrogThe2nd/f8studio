import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { AdminGate } from './app/AdminGate.jsx';
import { AuthGate } from './app/AuthGate.jsx';
import { Layout } from './app/Layout.jsx';
import { PublicLayout } from './app/PublicLayout.jsx';
import { SessionProvider, useSession } from './hooks/useSession.jsx';
import { AuthCallbackRoute } from './routes/auth-callback.jsx';
import { AssetDetailRoute } from './routes/asset-detail.jsx';
import { BrowseRoute } from './routes/browse.jsx';
import { ForgotPasswordRoute } from './routes/forgot-password.jsx';
import { LoginRoute } from './routes/login.jsx';
import { MyAssetsRoute } from './routes/my-assets.jsx';
import { NotFoundRoute } from './routes/not-found.jsx';
import { ProfileRoute } from './routes/profile.jsx';
import { RegisterRoute } from './routes/register.jsx';
import { ResetPasswordRoute } from './routes/reset-password.jsx';
import { SettingsRoute } from './routes/settings.jsx';
import { UsersRoute } from './routes/users.jsx';
import { VerifyEmailRoute } from './routes/verify-email.jsx';
import './index.css';

function IndexRedirect() {
  const { isAuthenticated, authResolved } = useSession();
  if (!authResolved) {
    return <PublicLayout title="Loading portal" subtitle="Restoring your AssetCloud session." />;
  }
  return <Navigate replace to={isAuthenticated ? '/assets/mine' : '/browse'} />;
}

function BrowseShell() {
  const { authResolved, isAuthenticated } = useSession();

  if (!authResolved) {
    return <PublicLayout title="Loading portal" subtitle="Restoring your AssetCloud session." />;
  }
  if (isAuthenticated) {
    return (
      <Layout>
        <BrowseRoute />
      </Layout>
    );
  }
  return (
    <PublicLayout title="Browse Assets" subtitle="Explore public assets from the portal without signing in.">
      <div className="w-full">
        <BrowseRoute />
      </div>
    </PublicLayout>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <Routes>
          <Route path="/" element={<IndexRedirect />} />
          <Route element={<PublicLayout />}>
            <Route path="/login" element={<LoginRoute />} />
            <Route path="/register" element={<RegisterRoute />} />
            <Route path="/forgot-password" element={<ForgotPasswordRoute />} />
            <Route path="/reset-password" element={<ResetPasswordRoute />} />
            <Route path="/verify-email" element={<VerifyEmailRoute />} />
            <Route path="/auth-callback" element={<AuthCallbackRoute />} />
            <Route path="/auth-complete" element={<Navigate replace to="/auth-callback?status=success" />} />
            <Route path="/auth-error" element={<Navigate replace to="/auth-callback?status=error" />} />
          </Route>
          <Route path="/browse" element={<BrowseShell />} />
          <Route path="/assets/:assetId" element={<AssetDetailRoute />} />
          <Route element={<AuthGate />}>
            <Route element={<Layout />}>
              <Route path="/assets/mine" element={<MyAssetsRoute />} />
              <Route path="/profile" element={<ProfileRoute />} />
              <Route element={<AdminGate />}>
                <Route path="/admin/users" element={<UsersRoute />} />
                <Route path="/admin/settings" element={<SettingsRoute />} />
              </Route>
            </Route>
          </Route>
          <Route path="*" element={<NotFoundRoute />} />
        </Routes>
      </SessionProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
