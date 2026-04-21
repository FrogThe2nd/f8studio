import { createContext, useContext, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { authClient } from '../authClient.js';
import { getAuthProviders, getCurrentUser, getSiteSettings } from '../lib/api.js';

const SessionContext = createContext(null);

export function SessionProvider({ children }) {
  const sessionQuery = authClient.useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [authProviders, setAuthProviders] = useState({ google: false });
  const [siteSettings, setSiteSettings] = useState({ allowUserRegistration: false });
  const [currentUser, setCurrentUser] = useState(null);
  const [linkedAccounts, setLinkedAccounts] = useState([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState('');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [providers, settings] = await Promise.all([
          getAuthProviders(),
          getSiteSettings(),
        ]);
        if (cancelled) {
          return;
        }
        setAuthProviders({
          google: Boolean(providers?.google),
        });
        setSiteSettings({
          allowUserRegistration: Boolean(settings?.allowUserRegistration),
        });
      } catch {
        if (cancelled) {
          return;
        }
        setAuthProviders({ google: false });
        setSiteSettings({ allowUserRegistration: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshProfile() {
    setProfileLoading(true);
    setProfileError('');
    try {
      const [user, accounts] = await Promise.all([
        getCurrentUser(),
        authClient.listAccounts(),
      ]);
      setCurrentUser(user);
      setLinkedAccounts(Array.isArray(accounts) ? accounts : []);
      return user;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setProfileError(message);
      setCurrentUser(null);
      setLinkedAccounts([]);
      throw error;
    } finally {
      setProfileLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    if (!sessionQuery.data?.user) {
      setCurrentUser(null);
      setLinkedAccounts([]);
      setProfileError('');
      setProfileLoading(false);
      return undefined;
    }
    setProfileLoading(true);
    setProfileError('');
    void (async () => {
      try {
        const [user, accounts] = await Promise.all([
          getCurrentUser(),
          authClient.listAccounts(),
        ]);
        if (cancelled) {
          return;
        }
        setCurrentUser(user);
        setLinkedAccounts(Array.isArray(accounts) ? accounts : []);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setCurrentUser(null);
        setLinkedAccounts([]);
        setProfileError(error instanceof Error ? error.message : String(error));
      } finally {
        if (!cancelled) {
          setProfileLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionQuery.data?.user?.id]);

  async function signOut() {
    await authClient.signOut();
    await sessionQuery.refetch();
    setCurrentUser(null);
    setLinkedAccounts([]);
    setProfileError('');
    if (location.pathname !== '/login') {
      navigate('/login');
    }
  }

  const value = {
    authProviders,
    authResolved: !sessionQuery.isPending,
    currentUser,
    isAuthenticated: Boolean(sessionQuery.data?.user),
    linkedAccounts,
    profileError,
    profileLoading,
    refreshProfile,
    sessionQuery,
    signOut,
    siteSettings,
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error('useSession must be used within SessionProvider');
  }
  return context;
}
