import React, { Suspense, lazy } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';

import { AdminGate } from './app/AdminGate.jsx';
import { AuthGate } from './app/AuthGate.jsx';
import { Layout } from './app/Layout.jsx';
import { PublicLayout } from './app/PublicLayout.jsx';
import { SessionProvider, useSession } from './hooks/useSession.jsx';
import './index.css';

function lazyRoute(loader, exportName) {
  return lazy(async () => {
    const module = await loader();
    return {
      default: module[exportName],
    };
  });
}

const AuthCallbackRoute = lazyRoute(() => import('./routes/auth-callback.jsx'), 'AuthCallbackRoute');
const AssetDetailRoute = lazyRoute(() => import('./routes/asset-detail.jsx'), 'AssetDetailRoute');
const BrowseRoute = lazyRoute(() => import('./routes/browse.jsx'), 'BrowseRoute');
const ForgotPasswordRoute = lazyRoute(() => import('./routes/forgot-password.jsx'), 'ForgotPasswordRoute');
const LoginRoute = lazyRoute(() => import('./routes/login.jsx'), 'LoginRoute');
const MyAssetsRoute = lazyRoute(() => import('./routes/my-assets.jsx'), 'MyAssetsRoute');
const NotFoundRoute = lazyRoute(() => import('./routes/not-found.jsx'), 'NotFoundRoute');
const ProfileRoute = lazyRoute(() => import('./routes/profile.jsx'), 'ProfileRoute');
const RegisterRoute = lazyRoute(() => import('./routes/register.jsx'), 'RegisterRoute');
const ResetPasswordRoute = lazyRoute(() => import('./routes/reset-password.jsx'), 'ResetPasswordRoute');
const SettingsRoute = lazyRoute(() => import('./routes/settings.jsx'), 'SettingsRoute');
const UsersRoute = lazyRoute(() => import('./routes/users.jsx'), 'UsersRoute');
const VerifyEmailRoute = lazyRoute(() => import('./routes/verify-email.jsx'), 'VerifyEmailRoute');

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

function RouteLoader() {
  return <p className="text-sm text-slate-300">Loading view...</p>;
}

function LazyRoute({ component: Component }) {
  return (
    <Suspense fallback={<RouteLoader />}>
      <Component />
    </Suspense>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <Routes>
          <Route path="/" element={<IndexRedirect />} />
          <Route element={<PublicLayout />}>
            <Route path="/login" element={<LazyRoute component={LoginRoute} />} />
            <Route path="/register" element={<LazyRoute component={RegisterRoute} />} />
            <Route path="/forgot-password" element={<LazyRoute component={ForgotPasswordRoute} />} />
            <Route path="/reset-password" element={<LazyRoute component={ResetPasswordRoute} />} />
            <Route path="/verify-email" element={<LazyRoute component={VerifyEmailRoute} />} />
            <Route path="/auth-callback" element={<LazyRoute component={AuthCallbackRoute} />} />
            <Route path="/auth-complete" element={<Navigate replace to="/auth-callback?status=success" />} />
            <Route path="/auth-error" element={<Navigate replace to="/auth-callback?status=error" />} />
          </Route>
          <Route path="/browse" element={<LazyRoute component={BrowseShell} />} />
          <Route path="/assets/:assetId" element={<LazyRoute component={AssetDetailRoute} />} />
          <Route element={<AuthGate />}>
            <Route element={<Layout />}>
              <Route path="/assets/mine" element={<LazyRoute component={MyAssetsRoute} />} />
              <Route path="/profile" element={<LazyRoute component={ProfileRoute} />} />
              <Route element={<AdminGate />}>
                <Route path="/admin/users" element={<LazyRoute component={UsersRoute} />} />
                <Route path="/admin/settings" element={<LazyRoute component={SettingsRoute} />} />
              </Route>
            </Route>
          </Route>
          <Route path="*" element={<LazyRoute component={NotFoundRoute} />} />
        </Routes>
      </SessionProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
