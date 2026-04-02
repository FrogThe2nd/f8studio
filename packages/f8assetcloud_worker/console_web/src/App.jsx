import { useEffect, useMemo, useRef, useState } from 'react';

import { authClient } from './authClient.js';

const CONSOLE_BASE_PATH = '/console';
const VERIFY_EMAIL_PATH = `${CONSOLE_BASE_PATH}/verify-email`;
const RESET_PASSWORD_PATH = `${CONSOLE_BASE_PATH}/reset-password`;
const MANAGEMENT_API_BASE_PATH = '/v1/management';

async function parseJsonResponse(response) {
  const text = await response.text();
  if (!text) {
    return {};
  }
  return JSON.parse(text);
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ConsoleRootApp() {
  const route = resolveRoute(window.location.pathname);
  if (route === 'verify-email') {
    return <VerifyEmailPage />;
  }
  if (route === 'reset-password') {
    return <ResetPasswordPage />;
  }
  return <ConsoleApp />;
}

function ConsoleApp() {
  const sessionQuery = authClient.useSession();
  const session = sessionQuery.data;
  const isLoggedIn = Boolean(session?.user);
  const registerUsernameAbortRef = useRef(null);

  const [authMode, setAuthMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false);
  const [registerDisplayName, setRegisterDisplayName] = useState('');
  const [registerEmail, setRegisterEmail] = useState('');
  const [registerUsername, setRegisterUsername] = useState('');
  const [registerPassword, setRegisterPassword] = useState('');
  const [registerConfirmPassword, setRegisterConfirmPassword] = useState('');
  const [registerPasswordVisible, setRegisterPasswordVisible] = useState(false);
  const [registerConfirmPasswordVisible, setRegisterConfirmPasswordVisible] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const [currentUser, setCurrentUser] = useState(null);
  const [activePage, setActivePage] = useState('profile');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const [linkedAccounts, setLinkedAccounts] = useState([]);
  const [usernameAvailability, setUsernameAvailability] = useState({
    state: 'idle',
    message: 'Choose a username with letters, numbers, or underscores.',
  });

  const [mineAssetType, setMineAssetType] = useState('variant');
  const [mineQuery, setMineQuery] = useState('');
  const [mineAssets, setMineAssets] = useState([]);

  const [allAssetType, setAllAssetType] = useState('variant');
  const [allQuery, setAllQuery] = useState('');
  const [allAssets, setAllAssets] = useState([]);

  const [users, setUsers] = useState([]);
  const [userQuery, setUserQuery] = useState('');
  const [editingUserId, setEditingUserId] = useState('');
  const [editUsername, setEditUsername] = useState('');
  const [editDisplayName, setEditDisplayName] = useState('');
  const [editHasManagementAccess, setEditHasManagementAccess] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newHasManagementAccess, setNewHasManagementAccess] = useState(false);

  const [currentPassword, setCurrentPassword] = useState('');
  const [nextPassword, setNextPassword] = useState('');
  const [confirmNextPassword, setConfirmNextPassword] = useState('');
  const [currentPasswordVisible, setCurrentPasswordVisible] = useState(false);
  const [nextPasswordVisible, setNextPasswordVisible] = useState(false);
  const [confirmNextPasswordVisible, setConfirmNextPasswordVisible] = useState(false);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const response = await fetch('/v1/auth/providers');
        const data = await parseJsonResponse(response);
        if (active) {
          setGoogleEnabled(Boolean(data.google));
        }
      } catch (error) {
        if (active) {
          setGoogleEnabled(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!isLoggedIn) {
      setCurrentUser(null);
      setLinkedAccounts([]);
      setUsers([]);
      setMineAssets([]);
      setAllAssets([]);
      if (activePage === 'users') {
        setActivePage('profile');
      }
    }
  }, [activePage, isLoggedIn]);

  useEffect(() => {
    if (authMode !== 'register') {
      if (registerUsernameAbortRef.current) {
        registerUsernameAbortRef.current.abort();
        registerUsernameAbortRef.current = null;
      }
      setUsernameAvailability({
        state: 'idle',
        message: 'Choose a username with letters, numbers, or underscores.',
      });
      return;
    }

    const normalizedUsername = registerUsername.trim();
    if (!normalizedUsername) {
      setUsernameAvailability({
        state: 'idle',
        message: 'Username is required.',
      });
      return;
    }
    if (!/^[A-Za-z0-9_]{3,64}$/.test(normalizedUsername)) {
      setUsernameAvailability({
        state: 'invalid',
        message: 'Use 3-64 letters, numbers, or underscores.',
      });
      return;
    }

    const controller = new AbortController();
    registerUsernameAbortRef.current = controller;
    const timeoutId = window.setTimeout(async () => {
      setUsernameAvailability({
        state: 'checking',
        message: 'Checking availability...',
      });
      try {
        const response = await fetch('/api/auth/is-username-available', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ username: normalizedUsername }),
          signal: controller.signal,
        });
        const data = await parseJsonResponse(response);
        if (!response.ok) {
          throw new Error(data.message || 'Unable to validate username.');
        }
        setUsernameAvailability(data.available ? {
          state: 'available',
          message: 'Username is available.',
        } : {
          state: 'taken',
          message: 'Username is already taken.',
        });
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        setUsernameAvailability({
          state: 'error',
          message: error instanceof Error ? error.message : String(error),
        });
      }
    }, 350);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [authMode, registerUsername]);

  async function apiRequest(path, options = {}) {
    const headers = {};
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, {
      ...options,
      headers,
    });
    const data = await parseJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.message || `Request failed (${response.status})`);
    }
    return data;
  }

  async function loadProfile() {
    const me = await apiRequest('/v1/me');
    setCurrentUser(me);
  }

  async function loadLinkedAccounts() {
    const accounts = await apiRequest('/api/auth/list-accounts');
    setLinkedAccounts(Array.isArray(accounts) ? accounts : []);
  }

  async function loadMineAssets() {
    const result = await apiRequest(
      `/v1/search?assetType=${encodeURIComponent(mineAssetType)}&owner=me&q=${encodeURIComponent(mineQuery.trim())}`,
    );
    setMineAssets(result.entries || []);
  }

  async function loadAllAssets() {
    const result = await apiRequest(
      `/v1/search?assetType=${encodeURIComponent(allAssetType)}&owner=public&q=${encodeURIComponent(allQuery.trim())}`,
    );
    setAllAssets(result.entries || []);
  }

  async function loadUsers() {
    if (!currentUser?.isAdmin) {
      return;
    }
    const result = await apiRequest(`${MANAGEMENT_API_BASE_PATH}/users?q=${encodeURIComponent(userQuery.trim())}`);
    setUsers(result.entries || []);
  }

  async function refreshCurrentPage() {
    if (!isLoggedIn) {
      return;
    }
    setLoading(true);
    setStatusText('Loading...');
    try {
      if (activePage === 'profile') {
        await Promise.all([loadProfile(), loadLinkedAccounts()]);
      }
      if (activePage === 'my-assets') {
        await loadMineAssets();
      }
      if (activePage === 'all-assets') {
        await loadAllAssets();
      }
      if (activePage === 'users') {
        await loadUsers();
      }
      setStatusText('Loaded');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!isLoggedIn) {
      return;
    }
    void refreshCurrentPage();
  }, [isLoggedIn, activePage, mineAssetType, allAssetType]);

  async function onLogin(event) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setStatusText('Username and password are required.');
      return;
    }
    setLoading(true);
    setStatusText('Signing in...');
    try {
      await authClient.signIn.username({
        username: username.trim(),
        password,
      });
      await sessionQuery.refetch();
      await Promise.all([loadProfile(), loadLinkedAccounts()]);
      setPassword('');
      setActivePage('profile');
      setStatusText(`Signed in as ${username.trim()}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onGoogleSignIn() {
    setLoading(true);
    setStatusText('Redirecting to Google...');
    try {
      await authClient.signIn.social({
        provider: 'google',
        callbackURL: `${window.location.origin}${CONSOLE_BASE_PATH}/`,
      });
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
      setLoading(false);
    }
  }

  async function onLinkGoogle() {
    setLoading(true);
    setStatusText('Redirecting to Google account linking...');
    try {
      const result = await apiRequest('/api/auth/link-social', {
        method: 'POST',
        body: JSON.stringify({
          provider: 'google',
          callbackURL: `${window.location.origin}${CONSOLE_BASE_PATH}/`,
          disableRedirect: true,
        }),
      });
      if (result.url) {
        window.location.assign(String(result.url));
        return;
      }
      await Promise.all([sessionQuery.refetch(), loadProfile(), loadLinkedAccounts()]);
      setStatusText('Google account linked');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onRegister(event) {
    event.preventDefault();
    if (!registerEmail.trim() || !registerUsername.trim() || !registerPassword) {
      setStatusText('Email, username, and password are required.');
      return;
    }
    if (registerPassword !== registerConfirmPassword) {
      setStatusText('Registration passwords do not match.');
      return;
    }
    if (usernameAvailability.state === 'checking') {
      setStatusText('Please wait until username availability is checked.');
      return;
    }
    if (usernameAvailability.state === 'taken' || usernameAvailability.state === 'invalid') {
      setStatusText(usernameAvailability.message);
      return;
    }
    const normalizedDisplayName = registerDisplayName.trim() || registerUsername.trim();
    setLoading(true);
    setStatusText('Creating account...');
    try {
      await authClient.signUp.email({
        name: normalizedDisplayName,
        email: registerEmail.trim(),
        username: registerUsername.trim(),
        displayUsername: normalizedDisplayName,
        password: registerPassword,
        callbackURL: `${window.location.origin}${VERIFY_EMAIL_PATH}?verified=1`,
      });
      setRegisterDisplayName('');
      setRegisterEmail('');
      setRegisterUsername('');
      setRegisterPassword('');
      setRegisterConfirmPassword('');
      setAuthMode('login');
      setStatusText('Account created. Please verify your email before signing in.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onForgotPassword(event) {
    event.preventDefault();
    if (!forgotEmail.trim()) {
      setStatusText('Email is required.');
      return;
    }
    setLoading(true);
    setStatusText('Submitting password reset request...');
    try {
      await authClient.requestPasswordReset({
        email: forgotEmail.trim(),
        redirectTo: `${window.location.origin}${RESET_PASSWORD_PATH}`,
      });
      setForgotEmail('');
      setAuthMode('login');
      setStatusText('If this email exists, a reset link has been sent.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onLogout() {
    setLoading(true);
    try {
      await authClient.signOut();
      await sessionQuery.refetch();
      setCurrentUser(null);
      setLinkedAccounts([]);
      setUsers([]);
      setMineAssets([]);
      setAllAssets([]);
      setCurrentPassword('');
      setNextPassword('');
      setConfirmNextPassword('');
      setStatusText('Logged out');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onChangePassword(event) {
    event.preventDefault();
    if (!hasCredentialAccount) {
      setStatusText('This account does not currently support password sign-in.');
      return;
    }
    if (!currentPassword || !nextPassword || !confirmNextPassword) {
      setStatusText('Current password, new password, and confirmation are required.');
      return;
    }
    if (nextPassword !== confirmNextPassword) {
      setStatusText('New passwords do not match.');
      return;
    }
    setLoading(true);
    setStatusText('Changing password...');
    try {
      await authClient.changePassword({
        currentPassword,
        newPassword: nextPassword,
        revokeOtherSessions: true,
      });
      setCurrentPassword('');
      setNextPassword('');
      setConfirmNextPassword('');
      setStatusText('Password updated');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onUnlinkAccount(account) {
    const providerLabel = formatAccountProvider(account.providerId);
    const confirmed = window.confirm(`Unlink ${providerLabel} from this account?`);
    if (!confirmed) {
      return;
    }
    setLoading(true);
    setStatusText(`Unlinking ${providerLabel}...`);
    try {
      await apiRequest('/api/auth/unlink-account', {
        method: 'POST',
        body: JSON.stringify({
          providerId: account.providerId,
          accountId: account.accountId,
        }),
      });
      await Promise.all([sessionQuery.refetch(), loadProfile(), loadLinkedAccounts()]);
      setStatusText(`${providerLabel} unlinked`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onDeleteOwnAsset(asset) {
    const confirmed = window.confirm(`Delete ${asset.assetType} ${asset.assetId}?`);
    if (!confirmed) {
      return;
    }
    setLoading(true);
    try {
      const endpoint = asset.assetType === 'variant'
        ? `/v1/variants/${encodeURIComponent(asset.assetId)}`
        : `/v1/components/${encodeURIComponent(asset.assetId)}`;
      await apiRequest(endpoint, { method: 'DELETE' });
      await loadMineAssets();
      setStatusText(`Deleted ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onDownloadAsset(asset) {
    try {
      const endpoint = asset.assetType === 'variant'
        ? `/v1/variants/${encodeURIComponent(asset.assetId)}`
        : `/v1/components/${encodeURIComponent(asset.assetId)}`;
      const detail = await apiRequest(endpoint);
      downloadJson(`${asset.assetType}-${asset.assetId}.json`, detail);
      setStatusText(`Downloaded ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    }
  }

  async function onToggleSubscribe(asset) {
    setLoading(true);
    try {
      const root = asset.assetType === 'variant' ? '/v1/variants' : '/v1/components';
      const endpoint = `${root}/${encodeURIComponent(asset.assetId)}/subscribe`;
      if (asset.subscribed) {
        await apiRequest(endpoint, { method: 'DELETE' });
      } else {
        await apiRequest(endpoint, { method: 'POST' });
      }
      await loadAllAssets();
      setStatusText(`${asset.subscribed ? 'Unsubscribed' : 'Subscribed'} ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onCreateUser(event) {
    event.preventDefault();
    if (!currentUser?.isAdmin) {
      return;
    }
    setLoading(true);
    try {
      await apiRequest(`${MANAGEMENT_API_BASE_PATH}/users`, {
        method: 'POST',
        body: JSON.stringify({
          username: newUsername.trim(),
          email: newEmail.trim(),
          password: newPassword,
          displayName: newDisplayName.trim() || newUsername.trim(),
          isAdmin: newHasManagementAccess,
        }),
      });
      setNewUsername('');
      setNewEmail('');
      setNewPassword('');
      setNewDisplayName('');
      setNewHasManagementAccess(false);
      await loadUsers();
      setStatusText('User created');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onDeleteUser(userId, name) {
    if (!currentUser?.isAdmin) {
      return;
    }
    const confirmed = window.confirm(`Delete user ${name}?`);
    if (!confirmed) {
      return;
    }
    setLoading(true);
    try {
      await apiRequest(`${MANAGEMENT_API_BASE_PATH}/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
      await loadUsers();
      setStatusText(`Deleted user ${name}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  function onBeginEditUser(user) {
    setEditingUserId(user.userId);
    setEditUsername(user.username);
    setEditDisplayName(user.displayName);
    setEditHasManagementAccess(Boolean(user.isAdmin));
  }

  function onCancelEditUser() {
    setEditingUserId('');
    setEditUsername('');
    setEditDisplayName('');
    setEditHasManagementAccess(false);
  }

  async function onUpdateUser(event) {
    event.preventDefault();
    if (!editingUserId) {
      return;
    }
    setLoading(true);
    try {
      await apiRequest(`${MANAGEMENT_API_BASE_PATH}/users/${encodeURIComponent(editingUserId)}`, {
        method: 'PUT',
        body: JSON.stringify({
          username: editUsername.trim(),
          displayName: editDisplayName.trim(),
          isAdmin: editHasManagementAccess,
        }),
      });
      onCancelEditUser();
      await loadUsers();
      setStatusText('User updated');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  const hasManagementAccess = Boolean(currentUser?.isAdmin);
  const hasCredentialAccount = linkedAccounts.some((account) => account.providerId === 'credential');
  const hasGoogleAccount = linkedAccounts.some((account) => account.providerId === 'google');
  const pageTitle = useMemo(() => {
    if (activePage === 'profile') {
      return 'Profile';
    }
    if (activePage === 'my-assets') {
      return 'My Assets';
    }
    if (activePage === 'all-assets') {
      return 'All Assets';
    }
    return 'User Management';
  }, [activePage]);

  if (sessionQuery.isPending || (isLoggedIn && currentUser === null)) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="status">Loading session...</p>
        </div>
      </div>
    );
  }

  if (!isLoggedIn) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="muted">
            {authMode === 'login' ? 'Sign in to continue.' : null}
            {authMode === 'register' ? 'Create a new account.' : null}
            {authMode === 'forgot' ? 'Request a password reset link by email.' : null}
          </p>
          {googleEnabled ? (
            <p className="muted social-hint">
              Google sign-in is available for direct login and for linking to an existing account.
            </p>
          ) : null}
          <p className="muted field-note">Fields marked * are required.</p>
          <div className="auth-switch">
            <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>Sign In</button>
            <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>Register</button>
            <button type="button" className={authMode === 'forgot' ? 'active' : ''} onClick={() => setAuthMode('forgot')}>Forgot Password</button>
          </div>
          <form onSubmit={authMode === 'login' ? onLogin : authMode === 'register' ? onRegister : onForgotPassword} className="form">
            {authMode === 'login' ? (
              <>
                <label>
                  Username *
                  <input
                    autoComplete="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="username"
                    required
                  />
                </label>
                <PasswordField
                  label="Password *"
                  autoComplete="current-password"
                  value={password}
                  onChange={setPassword}
                  placeholder="password"
                  visible={loginPasswordVisible}
                  onToggleVisibility={() => setLoginPasswordVisible((visible) => !visible)}
                  required
                />
              </>
            ) : null}
            {authMode === 'register' ? (
              <>
                <label>
                  Display Name
                  <input
                    value={registerDisplayName}
                    onChange={(event) => setRegisterDisplayName(event.target.value)}
                    placeholder="Optional, defaults to username"
                  />
                </label>
                <label>
                  Email *
                  <input
                    autoComplete="email"
                    type="email"
                    value={registerEmail}
                    onChange={(event) => setRegisterEmail(event.target.value)}
                    placeholder="you@example.com"
                    required
                  />
                </label>
                <label>
                  Username *
                  <input
                    autoComplete="username"
                    value={registerUsername}
                    onChange={(event) => setRegisterUsername(event.target.value)}
                    placeholder="username"
                    required
                  />
                </label>
                <FieldHint
                  tone={
                    usernameAvailability.state === 'available' ? 'success'
                      : usernameAvailability.state === 'taken' || usernameAvailability.state === 'invalid' || usernameAvailability.state === 'error' ? 'error'
                        : 'muted'
                  }
                >
                  {usernameAvailability.message}
                </FieldHint>
                <FieldHint>Display name supports Chinese and other normal text. Leave it empty to use your username.</FieldHint>
                <PasswordField
                  label="Password *"
                  autoComplete="new-password"
                  value={registerPassword}
                  onChange={setRegisterPassword}
                  placeholder="password"
                  visible={registerPasswordVisible}
                  onToggleVisibility={() => setRegisterPasswordVisible((visible) => !visible)}
                  required
                />
                <PasswordField
                  label="Confirm Password *"
                  autoComplete="new-password"
                  value={registerConfirmPassword}
                  onChange={setRegisterConfirmPassword}
                  placeholder="confirm password"
                  visible={registerConfirmPasswordVisible}
                  onToggleVisibility={() => setRegisterConfirmPasswordVisible((visible) => !visible)}
                  required
                />
                {registerConfirmPassword ? (
                  <FieldHint tone={registerPassword === registerConfirmPassword ? 'success' : 'error'}>
                    {registerPassword === registerConfirmPassword ? 'Passwords match.' : 'Passwords do not match.'}
                  </FieldHint>
                ) : null}
              </>
            ) : null}
            {authMode === 'forgot' ? (
              <label>
                Email *
                <input
                  autoComplete="email"
                  type="email"
                  value={forgotEmail}
                  onChange={(event) => setForgotEmail(event.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </label>
            ) : null}
            <button
              type="submit"
              disabled={
                loading
                || (authMode === 'register' && (
                  !registerEmail.trim()
                  || !registerUsername.trim()
                  || !registerPassword
                  || !registerConfirmPassword
                  || registerPassword !== registerConfirmPassword
                  || usernameAvailability.state === 'checking'
                  || usernameAvailability.state === 'taken'
                  || usernameAvailability.state === 'invalid'
                ))
              }
            >
              {authMode === 'login' ? 'Sign In' : null}
              {authMode === 'register' ? 'Create Account' : null}
              {authMode === 'forgot' ? 'Send Reset Link' : null}
            </button>
          </form>
          {googleEnabled && authMode === 'login' ? (
            <div className="form">
              <button type="button" onClick={() => void onGoogleSignIn()} disabled={loading}>
                Continue with Google
              </button>
            </div>
          ) : null}
          <p className="status">{statusText}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="hero card">
        <div>
          <span className="eyebrow">Feel8 Console</span>
          <h1>{pageTitle}</h1>
          <p className="muted">Welcome, {currentUser?.displayName || currentUser?.username}</p>
        </div>
        <div className="actions">
          <button type="button" onClick={() => void refreshCurrentPage()} disabled={loading}>Refresh</button>
          <button type="button" onClick={() => void onLogout()} disabled={loading}>Logout</button>
        </div>
      </header>

      <div className="layout">
        <aside className="card panel nav-panel">
          <button
            type="button"
            className={activePage === 'profile' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('profile')}
          >
            Profile
          </button>
          <button
            type="button"
            className={activePage === 'my-assets' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('my-assets')}
          >
            My Assets
          </button>
          <button
            type="button"
            className={activePage === 'all-assets' ? 'nav-btn active' : 'nav-btn'}
            onClick={() => setActivePage('all-assets')}
          >
            All Assets
          </button>
          {hasManagementAccess ? (
            <button
              type="button"
              className={activePage === 'users' ? 'nav-btn active' : 'nav-btn'}
              onClick={() => setActivePage('users')}
            >
              User Management
            </button>
          ) : null}
          <p className="status">{statusText}</p>
        </aside>

        <main className="stack">
          {activePage === 'profile' ? (
            <section className="card panel">
              <h2>Profile</h2>
              <div className="profile-grid">
                <div><strong>Username:</strong> {currentUser?.username}</div>
                <div><strong>Display Name:</strong> {currentUser?.displayName}</div>
              <div><strong>Email:</strong> {currentUser?.email}</div>
              <div><strong>Email Verified:</strong> {currentUser?.emailVerified ? 'Yes' : 'No'}</div>
              <div><strong>Access Level:</strong> {hasManagementAccess ? 'Management' : 'User'}</div>
            </div>
              <div className="auth-methods">
                <div className="section-head">
                  <h3>Sign-In Methods</h3>
                  <div className="inline-form">
                    <button type="button" onClick={() => void loadLinkedAccounts()} disabled={loading}>Refresh Methods</button>
                    {googleEnabled && !hasGoogleAccount ? (
                      <button type="button" onClick={() => void onLinkGoogle()} disabled={loading}>
                        Link Google
                      </button>
                    ) : null}
                  </div>
                </div>
                {linkedAccounts.length > 0 ? (
                  <div className="account-chip-list">
                    {linkedAccounts.map((account) => (
                      <div className="account-chip" key={`${account.providerId}:${account.accountId}`}>
                        <div>
                          <strong>{formatAccountProvider(account.providerId)}</strong>
                          <div className="muted">
                            {account.providerId === 'credential' ? 'Password sign-in enabled' : `Connected as ${account.accountId}`}
                          </div>
                        </div>
                        {account.providerId !== 'credential' ? (
                          <button type="button" onClick={() => void onUnlinkAccount(account)} disabled={loading}>
                            Unlink
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted">No linked sign-in methods found yet.</p>
                )}
                {!hasCredentialAccount ? (
                  <p className="muted">
                    This account currently does not have password sign-in enabled. Use a linked provider such as Google to continue signing in.
                  </p>
                ) : null}
              </div>
              <form onSubmit={onChangePassword} className="form">
                <p className="muted field-note">Fields marked * are required.</p>
                <PasswordField
                  label="Current Password *"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={setCurrentPassword}
                  visible={currentPasswordVisible}
                  onToggleVisibility={() => setCurrentPasswordVisible((visible) => !visible)}
                  disabled={!hasCredentialAccount}
                  required
                />
                <PasswordField
                  label="New Password *"
                  autoComplete="new-password"
                  value={nextPassword}
                  onChange={setNextPassword}
                  visible={nextPasswordVisible}
                  onToggleVisibility={() => setNextPasswordVisible((visible) => !visible)}
                  disabled={!hasCredentialAccount}
                  required
                />
                <PasswordField
                  label="Confirm New Password *"
                  autoComplete="new-password"
                  value={confirmNextPassword}
                  onChange={setConfirmNextPassword}
                  visible={confirmNextPasswordVisible}
                  onToggleVisibility={() => setConfirmNextPasswordVisible((visible) => !visible)}
                  disabled={!hasCredentialAccount}
                  required
                />
                {confirmNextPassword ? (
                  <FieldHint tone={nextPassword === confirmNextPassword ? 'success' : 'error'}>
                    {nextPassword === confirmNextPassword ? 'Passwords match.' : 'Passwords do not match.'}
                  </FieldHint>
                ) : null}
                <button
                  type="submit"
                  disabled={loading || !hasCredentialAccount || !currentPassword || !nextPassword || !confirmNextPassword || nextPassword !== confirmNextPassword}
                >
                  Change Password
                </button>
              </form>
            </section>
          ) : null}

          {activePage === 'my-assets' ? (
            <section className="card panel">
              <div className="section-head">
                <h2>My Assets</h2>
                <div className="inline-form">
                  <input
                    value={mineQuery}
                    onChange={(event) => setMineQuery(event.target.value)}
                    placeholder="Search my assets"
                  />
                  <select value={mineAssetType} onChange={(event) => setMineAssetType(event.target.value)}>
                    <option value="variant">variant</option>
                    <option value="component">component</option>
                  </select>
                  <button type="button" onClick={() => void loadMineAssets()} disabled={loading}>Search</button>
                </div>
              </div>
              <SimpleTable
                columns={['Asset ID', 'Type', 'Visibility', 'Revision', 'Actions']}
                rows={mineAssets.map((asset) => [
                  asset.assetId,
                  asset.assetType,
                  asset.visibility,
                  asset.revision,
                  <div className="inline-form" key={`${asset.assetId}-actions`}>
                    <button type="button" onClick={() => void onDownloadAsset(asset)}>Download</button>
                    <button type="button" onClick={() => void onDeleteOwnAsset(asset)} disabled={loading}>Delete</button>
                  </div>,
                ])}
                emptyText="No assets"
              />
            </section>
          ) : null}

          {activePage === 'all-assets' ? (
            <section className="card panel">
              <div className="section-head">
                <h2>All Assets</h2>
                <div className="inline-form">
                  <input
                    value={allQuery}
                    onChange={(event) => setAllQuery(event.target.value)}
                    placeholder="Search public assets"
                  />
                  <select value={allAssetType} onChange={(event) => setAllAssetType(event.target.value)}>
                    <option value="variant">variant</option>
                    <option value="component">component</option>
                  </select>
                  <button type="button" onClick={() => void loadAllAssets()} disabled={loading}>Search</button>
                </div>
              </div>
              <SimpleTable
                columns={['Asset ID', 'Type', 'Owner', 'Subscribed', 'Actions']}
                rows={allAssets.map((asset) => [
                  asset.assetId,
                  asset.assetType,
                  asset.ownerDisplayName || asset.ownerUserId,
                  asset.subscribed ? 'Yes' : 'No',
                  <button
                    type="button"
                    key={`${asset.assetId}-sub`}
                    onClick={() => void onToggleSubscribe(asset)}
                    disabled={loading}
                  >
                    {asset.subscribed ? 'Unsubscribe' : 'Subscribe'}
                  </button>,
                ])}
                emptyText="No assets"
              />
            </section>
          ) : null}

          {activePage === 'users' && hasManagementAccess ? (
            <section className="card panel">
              <div className="section-head">
                <h2>User Management</h2>
                <div className="inline-form">
                  <input
                    value={userQuery}
                    onChange={(event) => setUserQuery(event.target.value)}
                    placeholder="Search users"
                  />
                  <button type="button" onClick={() => void loadUsers()} disabled={loading}>Search</button>
                </div>
              </div>
              <form onSubmit={onCreateUser} className="form">
                <label>
                  New Username
                  <input
                    autoComplete="username"
                    value={newUsername}
                    onChange={(event) => setNewUsername(event.target.value)}
                  />
                </label>
                <label>
                  Email
                  <input
                    autoComplete="email"
                    type="email"
                    value={newEmail}
                    onChange={(event) => setNewEmail(event.target.value)}
                  />
                </label>
                <label>
                  New Password
                  <input
                    autoComplete="new-password"
                    type="password"
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                  />
                </label>
                <label>
                  Display Name
                  <input
                    value={newDisplayName}
                    onChange={(event) => setNewDisplayName(event.target.value)}
                  />
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={newHasManagementAccess}
                    onChange={(event) => setNewHasManagementAccess(event.target.checked)}
                  />
                  {' '}
                  Grant management access
                </label>
                <button type="submit" disabled={loading || !newUsername.trim() || !newEmail.trim() || !newPassword}>Create User</button>
              </form>
              {editingUserId ? (
                <form onSubmit={onUpdateUser} className="form">
                  <h3>Edit User</h3>
                  <label>
                    Username
                    <input
                      autoComplete="username"
                      value={editUsername}
                      onChange={(event) => setEditUsername(event.target.value)}
                    />
                  </label>
                  <label>
                    Display Name
                    <input
                      value={editDisplayName}
                      onChange={(event) => setEditDisplayName(event.target.value)}
                    />
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={editHasManagementAccess}
                      onChange={(event) => setEditHasManagementAccess(event.target.checked)}
                    />
                    {' '}
                    Management access
                  </label>
                  <div className="inline-form">
                    <button type="submit" disabled={loading || !editUsername.trim() || !editDisplayName.trim()}>Save Changes</button>
                    <button type="button" onClick={onCancelEditUser} disabled={loading}>Cancel</button>
                  </div>
                </form>
              ) : null}
              <SimpleTable
                columns={['Username', 'Email', 'Display Name', 'Access', 'Assets', 'Created At', 'Actions']}
                rows={users.map((user) => [
                  user.username,
                  user.email || '',
                  user.displayName,
                  user.isAdmin ? 'Yes' : 'No',
                  String(user.assetCount || 0),
                  user.createdAt,
                  <div className="inline-form" key={`${user.userId}-actions`}>
                    <button
                      type="button"
                      onClick={() => onBeginEditUser(user)}
                      disabled={loading}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => void onDeleteUser(user.userId, user.username)}
                      disabled={loading}
                    >
                      Delete
                    </button>
                  </div>,
                ])}
                emptyText="No users"
              />
            </section>
          ) : null}
        </main>
      </div>
    </div>
  );
}

function VerifyEmailPage() {
  const searchParams = new URL(window.location.href).searchParams;
  const [initialToken] = useState(() => searchParams.get('token') || '');
  const [token, setToken] = useState(initialToken);
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState(() => {
    if (searchParams.get('verified')) {
      return 'Email verified. You can sign in now.';
    }
    const error = searchParams.get('error');
    if (error) {
      return `Verification failed: ${error}`;
    }
    return 'Ready';
  });
  const [verified, setVerified] = useState(() => Boolean(searchParams.get('verified')));
  const autoVerifyStarted = useRef(false);

  useEffect(() => {
    if (autoVerifyStarted.current || !initialToken.trim()) {
      return;
    }
    autoVerifyStarted.current = true;
    void onVerify(initialToken.trim());
  }, [initialToken]);

  async function apiRequest(path, options = {}) {
    const headers = {};
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, {
      ...options,
      headers,
    });
    const data = await parseJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.message || `Request failed (${response.status})`);
    }
    return data;
  }

  async function onVerify(submitToken = token) {
    if (!String(submitToken || '').trim()) {
      setStatusText('token is required');
      return;
    }
    setLoading(true);
    setStatusText('Verifying email...');
    try {
      await apiRequest(`/v1/auth/verify-email?token=${encodeURIComponent(submitToken.trim())}`);
      setVerified(true);
      setStatusText('Email verified. You can sign in now.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="shell login-shell auth-page-shell">
      <div className="card panel login-card auth-page-card">
        <span className="eyebrow">Feel8 Account</span>
        <h1>Verify Email</h1>
        <p className="muted">Confirm your account email before signing in.</p>
        {!verified ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void onVerify();
            }}
            className="form"
          >
            <p className="muted field-note">Paste a token only if you opened this page manually.</p>
            <label>
              Verification Token
              <input
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="token from email"
              />
            </label>
            <button type="submit" disabled={loading || !token.trim()}>Verify Email</button>
          </form>
        ) : null}
        <div className="auth-links">
          <a href={`${CONSOLE_BASE_PATH}/`}>Back to Sign In</a>
          <a href={RESET_PASSWORD_PATH}>Reset Password</a>
        </div>
        {verified ? <div className="auth-result">Email verification completed.</div> : null}
        <p className="status">{statusText}</p>
      </div>
    </div>
  );
}

function ResetPasswordPage() {
  const searchParams = new URL(window.location.href).searchParams;
  const [token, setToken] = useState(() => searchParams.get('token') || '');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [newPasswordVisible, setNewPasswordVisible] = useState(false);
  const [confirmPasswordVisible, setConfirmPasswordVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState(() => {
    const error = searchParams.get('error');
    if (error) {
      return `Reset link error: ${error}`;
    }
    return 'Ready';
  });
  const [completed, setCompleted] = useState(false);

  async function apiRequest(path, options = {}) {
    const headers = {};
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, {
      ...options,
      headers,
    });
    const data = await parseJsonResponse(response);
    if (!response.ok) {
      throw new Error(data.message || `Request failed (${response.status})`);
    }
    return data;
  }

  async function onReset(event) {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setStatusText('Passwords do not match');
      return;
    }
    setLoading(true);
    setStatusText('Resetting password...');
    try {
      await apiRequest('/v1/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({
          token: token.trim(),
          newPassword,
        }),
      });
      setNewPassword('');
      setConfirmPassword('');
      setCompleted(true);
      setStatusText('Password updated. You can sign in now.');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="shell login-shell auth-page-shell">
      <div className="card panel login-card auth-page-card">
        <span className="eyebrow">Feel8 Account</span>
        <h1>Reset Password</h1>
        <p className="muted">Set a new password for your account.</p>
        <p className="muted field-note">Fields marked * are required.</p>
        <form onSubmit={onReset} className="form">
          <label>
            Reset Token *
            <input
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="token from email"
              required
            />
          </label>
          <PasswordField
            label="New Password *"
            autoComplete="new-password"
            value={newPassword}
            onChange={setNewPassword}
            placeholder="new password"
            visible={newPasswordVisible}
            onToggleVisibility={() => setNewPasswordVisible((visible) => !visible)}
            required
          />
          <PasswordField
            label="Confirm Password *"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={setConfirmPassword}
            placeholder="confirm password"
            visible={confirmPasswordVisible}
            onToggleVisibility={() => setConfirmPasswordVisible((visible) => !visible)}
            required
          />
          {confirmPassword ? (
            <FieldHint tone={newPassword === confirmPassword ? 'success' : 'error'}>
              {newPassword === confirmPassword ? 'Passwords match.' : 'Passwords do not match.'}
            </FieldHint>
          ) : null}
          <button type="submit" disabled={loading || !token.trim() || !newPassword || !confirmPassword || newPassword !== confirmPassword}>
            Reset Password
          </button>
        </form>
        <div className="auth-links">
          <a href={`${CONSOLE_BASE_PATH}/`}>Back to Sign In</a>
          <a href={VERIFY_EMAIL_PATH}>Verify Email</a>
        </div>
        {completed ? <div className="auth-result">Password reset completed.</div> : null}
        <p className="status">{statusText}</p>
      </div>
    </div>
  );
}

function resolveRoute(pathname) {
  const normalized = normalizePathname(pathname);
  const routePath = stripConsoleBasePath(normalized);
  if (routePath === '/verify-email') {
    return 'verify-email';
  }
  if (routePath === '/reset-password') {
    return 'reset-password';
  }
  return 'console';
}

function normalizePathname(pathname) {
  const text = String(pathname || '/');
  if (text.length > 1 && text.endsWith('/')) {
    return text.slice(0, -1);
  }
  return text;
}

function stripConsoleBasePath(pathname) {
  if (pathname === CONSOLE_BASE_PATH) {
    return '/';
  }
  if (pathname.startsWith(`${CONSOLE_BASE_PATH}/`)) {
    return pathname.slice(CONSOLE_BASE_PATH.length);
  }
  return pathname;
}

function formatAccountProvider(providerId) {
  if (providerId === 'credential') {
    return 'Password';
  }
  if (providerId === 'google') {
    return 'Google';
  }
  const text = String(providerId || '').trim();
  if (!text) {
    return 'Unknown';
  }
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

function PasswordField({
  label,
  value,
  onChange,
  visible,
  onToggleVisibility,
  autoComplete,
  placeholder,
  disabled = false,
  required = false,
}) {
  return (
    <label>
      {label}
      <div className="password-row">
        <input
          autoComplete={autoComplete}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          required={required}
        />
        <button type="button" className="password-toggle" onClick={onToggleVisibility} disabled={disabled}>
          {visible ? 'Hide' : 'Show'}
        </button>
      </div>
    </label>
  );
}

function FieldHint({ children, tone = 'muted' }) {
  const className = tone === 'success'
    ? 'field-hint field-hint-success'
    : tone === 'error'
      ? 'field-hint field-hint-error'
      : 'field-hint';
  return <div className={className}>{children}</div>;
}

function SimpleTable({ columns, rows, emptyText }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="muted">{emptyText}</td>
            </tr>
          ) : (
            rows.map((row, rowIndex) => (
              <tr key={String(rowIndex)}>
                {row.map((cell, cellIndex) => (
                  <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
