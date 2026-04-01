import { useEffect, useMemo, useState } from 'react';

function trimTrailingSlash(value) {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

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

export function AdminApp() {
  const [apiBase, setApiBase] = useState('');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [currentUser, setCurrentUser] = useState(null);
  const [activePage, setActivePage] = useState('profile');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');

  const [mineAssetType, setMineAssetType] = useState('variant');
  const [mineQuery, setMineQuery] = useState('');
  const [mineAssets, setMineAssets] = useState([]);

  const [allAssetType, setAllAssetType] = useState('variant');
  const [allQuery, setAllQuery] = useState('');
  const [allAssets, setAllAssets] = useState([]);

  const [users, setUsers] = useState([]);
  const [userQuery, setUserQuery] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newDisplayName, setNewDisplayName] = useState('');
  const [newIsAdmin, setNewIsAdmin] = useState(false);

  const [currentPassword, setCurrentPassword] = useState('');
  const [nextPassword, setNextPassword] = useState('');

  useEffect(() => {
    setToken(localStorage.getItem('feel8-admin-token') || '');
    setApiBase(localStorage.getItem('feel8-admin-base') || '');
  }, []);

  async function apiRequest(path, options = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const base = apiBase.trim() ? trimTrailingSlash(apiBase.trim()) : '';
    const response = await fetch(`${base}${path}`, {
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
    const result = await apiRequest(`/v1/admin/users?q=${encodeURIComponent(userQuery.trim())}`);
    setUsers(result.entries || []);
  }

  async function refreshCurrentPage() {
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
    if (!token) {
      return;
    }
    void refreshCurrentPage();
  }, [token, activePage, mineAssetType, allAssetType]);

  async function onLogin(event) {
    event.preventDefault();
    setLoading(true);
    setStatusText('Signing in...');
    try {
      const result = await apiRequest('/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), password }),
      });
      localStorage.setItem('feel8-admin-token', result.accessToken);
      localStorage.setItem('feel8-admin-base', apiBase.trim());
      setToken(result.accessToken);
      setCurrentUser(result.user || null);
      setActivePage('profile');
      setStatusText(`Signed in as ${result.user.username}`);
      await loadProfile();
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  function onLogout() {
    localStorage.removeItem('feel8-admin-token');
    setToken('');
    setCurrentUser(null);
    setUsers([]);
    setMineAssets([]);
    setAllAssets([]);
    setStatusText('Logged out');
  }

  async function onChangePassword(event) {
    event.preventDefault();
    setLoading(true);
    setStatusText('Changing password...');
    try {
      await apiRequest('/v1/me/password', {
        method: 'POST',
        body: JSON.stringify({
          currentPassword,
          newPassword: nextPassword,
        }),
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
      await apiRequest('/v1/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username: newUsername.trim(),
          password: newPassword,
          displayName: newDisplayName.trim() || newUsername.trim(),
          isAdmin: newIsAdmin,
        }),
      });
      setNewUsername('');
      setNewPassword('');
      setNewDisplayName('');
      setNewIsAdmin(false);
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
      await apiRequest(`/v1/admin/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
      await loadUsers();
      setStatusText(`Deleted user ${name}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  const isLoggedIn = Boolean(token);
  const isAdmin = Boolean(currentUser?.isAdmin);
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

  if (!isLoggedIn) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="muted">Sign in to continue.</p>
          <form onSubmit={onLogin} className="form">
            <label>
              API Base URL (optional)
              <input
                autoComplete="url"
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value)}
                placeholder="http://127.0.0.1:8787"
              />
            </label>
            <label>
              Username
              <input
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="admin"
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
            <button type="submit" disabled={loading}>Sign In</button>
          </form>
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
          <button type="button" onClick={onLogout} disabled={loading}>Logout</button>
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
          {isAdmin ? (
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
                <div><strong>Role:</strong> {isAdmin ? 'Admin' : 'User'}</div>
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

          {activePage === 'users' && isAdmin ? (
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
                    checked={newIsAdmin}
                    onChange={(event) => setNewIsAdmin(event.target.checked)}
                  />
                  {' '}
                  Create as admin
                </label>
                <button type="submit" disabled={loading || !newUsername.trim() || !newPassword}>Create User</button>
              </form>
              <SimpleTable
                columns={['Username', 'Display Name', 'Admin', 'Assets', 'Created At', 'Actions']}
                rows={users.map((user) => [
                  user.username,
                  user.displayName,
                  user.isAdmin ? 'Yes' : 'No',
                  String(user.assetCount || 0),
                  user.createdAt,
                  <button
                    type="button"
                    key={`${user.userId}-del`}
                    onClick={() => void onDeleteUser(user.userId, user.username)}
                    disabled={loading}
                  >
                    Delete
                  </button>,
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
