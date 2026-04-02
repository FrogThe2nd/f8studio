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

  const [authMode, setAuthMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [registerDisplayName, setRegisterDisplayName] = useState('');
  const [registerEmail, setRegisterEmail] = useState('');
  const [registerUsername, setRegisterUsername] = useState('');
  const [registerPassword, setRegisterPassword] = useState('');
  const [forgotEmail, setForgotEmail] = useState('');
  const [currentUser, setCurrentUser] = useState(null);
  const [activePage, setActivePage] = useState('profile');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');
  const [googleEnabled, setGoogleEnabled] = useState(false);

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
      setUsers([]);
      setMineAssets([]);
      setAllAssets([]);
      if (activePage === 'users') {
        setActivePage('profile');
      }
    }
  }, [activePage, isLoggedIn]);

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
        await loadProfile();
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
    setLoading(true);
    setStatusText('Signing in...');
    try {
      await authClient.signIn.username({
        username: username.trim(),
        password,
      });
      await sessionQuery.refetch();
      await loadProfile();
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

  async function onRegister(event) {
    event.preventDefault();
    setLoading(true);
    setStatusText('Creating account...');
    try {
      await authClient.signUp.email({
        name: registerDisplayName.trim(),
        email: registerEmail.trim(),
        username: registerUsername.trim(),
        displayUsername: registerDisplayName.trim(),
        password: registerPassword,
        callbackURL: `${window.location.origin}${VERIFY_EMAIL_PATH}`,
      });
      setRegisterDisplayName('');
      setRegisterEmail('');
      setRegisterUsername('');
      setRegisterPassword('');
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
      setUsers([]);
      setMineAssets([]);
      setAllAssets([]);
      setStatusText('Logged out');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onChangePassword(event) {
    event.preventDefault();
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
      setStatusText('Password updated');
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
          <div className="auth-switch">
            <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>Sign In</button>
            <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>Register</button>
            <button type="button" className={authMode === 'forgot' ? 'active' : ''} onClick={() => setAuthMode('forgot')}>Forgot Password</button>
          </div>
          <form onSubmit={authMode === 'login' ? onLogin : authMode === 'register' ? onRegister : onForgotPassword} className="form">
            {authMode === 'login' ? (
              <>
                <label>
                  Username
                  <input
                    autoComplete="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder="username"
                  />
                </label>
                <label>
                  Password
                  <input
                    autoComplete="current-password"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="password"
                  />
                </label>
              </>
            ) : null}
            {authMode === 'register' ? (
              <>
                <label>
                  Display Name
                  <input
                    value={registerDisplayName}
                    onChange={(event) => setRegisterDisplayName(event.target.value)}
                    placeholder="Your Name"
                  />
                </label>
                <label>
                  Email
                  <input
                    autoComplete="email"
                    type="email"
                    value={registerEmail}
                    onChange={(event) => setRegisterEmail(event.target.value)}
                    placeholder="you@example.com"
                  />
                </label>
                <label>
                  Username
                  <input
                    autoComplete="username"
                    value={registerUsername}
                    onChange={(event) => setRegisterUsername(event.target.value)}
                    placeholder="username"
                  />
                </label>
                <label>
                  Password
                  <input
                    autoComplete="new-password"
                    type="password"
                    value={registerPassword}
                    onChange={(event) => setRegisterPassword(event.target.value)}
                    placeholder="password"
                  />
                </label>
              </>
            ) : null}
            {authMode === 'forgot' ? (
              <label>
                Email
                <input
                  autoComplete="email"
                  type="email"
                  value={forgotEmail}
                  onChange={(event) => setForgotEmail(event.target.value)}
                  placeholder="you@example.com"
                />
              </label>
            ) : null}
            <button type="submit" disabled={loading}>
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
              <form onSubmit={onChangePassword} className="form">
                <label>
                  Current Password
                  <input
                    autoComplete="current-password"
                    type="password"
                    value={currentPassword}
                    onChange={(event) => setCurrentPassword(event.target.value)}
                  />
                </label>
                <label>
                  New Password
                  <input
                    autoComplete="new-password"
                    type="password"
                    value={nextPassword}
                    onChange={(event) => setNextPassword(event.target.value)}
                  />
                </label>
                <button type="submit" disabled={loading || !currentPassword || !nextPassword}>Change Password</button>
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
  const [initialToken] = useState(() => new URL(window.location.href).searchParams.get('token') || '');
  const [token, setToken] = useState(initialToken);
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');
  const [verified, setVerified] = useState(false);
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
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void onVerify();
          }}
          className="form"
        >
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
  const [token, setToken] = useState(() => new URL(window.location.href).searchParams.get('token') || '');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');
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
        <form onSubmit={onReset} className="form">
          <label>
            Reset Token
            <input
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="token from email"
            />
          </label>
          <label>
            New Password
            <input
              autoComplete="new-password"
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              placeholder="new password"
            />
          </label>
          <label>
            Confirm Password
            <input
              autoComplete="new-password"
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="confirm password"
            />
          </label>
          <button type="submit" disabled={loading || !token.trim() || !newPassword || !confirmPassword}>
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
