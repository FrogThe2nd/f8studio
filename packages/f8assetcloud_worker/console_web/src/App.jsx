import { useEffect, useMemo, useRef, useState } from 'react';

import { authClient } from './authClient.js';

const CONSOLE_BASE_PATH = '/console';
const CONSOLE_CALLBACK_PATH = `${CONSOLE_BASE_PATH}/`;
const VERIFY_EMAIL_PATH = `${CONSOLE_BASE_PATH}/verify-email`;
const RESET_PASSWORD_PATH = `${CONSOLE_BASE_PATH}/reset-password`;
const MANAGEMENT_API_BASE_PATH = '/v1/management';
const PURGE_ALL_ASSETS_CONFIRMATION_TEXT = 'DELETE ALL ASSETS';
const ASSET_TYPE_OPTIONS = ['component', 'variant'];
const USER_ROLE_OPTIONS = [
  { value: 'admin', label: 'Admin' },
  { value: 'user', label: 'User' },
  { value: 'readonly', label: 'Read Only' },
];

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

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function formatTimestampForDisplay(value) {
  const date = parseTimestampForDisplay(value);
  if (!date) {
    return String(value || '').trim();
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

export function formatTimestampTooltip(value) {
  const date = parseTimestampForDisplay(value);
  if (!date) {
    return String(value || '').trim();
  }
  const formattedParts = new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  }).formatToParts(date);
  return formattedParts.map((part) => part.value).join('');
}

function parseTimestampForDisplay(value) {
  const text = String(value || '').trim();
  if (!text) {
    return null;
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date;
}

export function downloadableContentForAsset(asset, payload) {
  if (!isPlainObject(payload)) {
    throw new Error('Asset content response must be a JSON object.');
  }
  const record = payload.record;
  if (!isPlainObject(record)) {
    throw new Error('Asset content response is missing record.');
  }

  const assetType = String(asset?.assetType || '');
  const assetId = String(asset?.assetId || '');
  if (assetType === 'component') {
    if (!isPlainObject(record.content)) {
      throw new Error('Component content response is missing record.content.');
    }
    return {
      filename: `component-${assetId}-content.json`,
      data: record.content,
    };
  }
  if (assetType === 'variant') {
    if (!isPlainObject(record.spec)) {
      throw new Error('Variant content response is missing record.spec.');
    }
    return {
      filename: `variant-${assetId}-spec.json`,
      data: record.spec,
    };
  }
  throw new Error(`Unsupported asset type: ${assetType || 'unknown'}`);
}

function managementCollectionPathForAssetType(assetType) {
  const normalizedAssetType = String(assetType || '').trim();
  if (normalizedAssetType === 'component') {
    return `${MANAGEMENT_API_BASE_PATH}/components`;
  }
  if (normalizedAssetType === 'variant') {
    return `${MANAGEMENT_API_BASE_PATH}/variants`;
  }
  throw new Error(`Unsupported asset type: ${normalizedAssetType || 'unknown'}`);
}

export function buildManagedAssetListPath(assetType, { ownerUserId = '', query = '', includeDeleted = false } = {}) {
  const params = new URLSearchParams();
  const normalizedOwnerUserId = String(ownerUserId || '').trim();
  const normalizedQuery = String(query || '').trim();
  if (normalizedOwnerUserId) {
    params.set('ownerUserId', normalizedOwnerUserId);
  }
  if (normalizedQuery) {
    params.set('q', normalizedQuery);
  }
  if (includeDeleted) {
    params.set('includeDeleted', 'true');
  }
  const basePath = managementCollectionPathForAssetType(assetType);
  const queryString = params.toString();
  return queryString ? `${basePath}?${queryString}` : basePath;
}

export function buildManagedAssetDetailPath(asset, { includeDeleted = false } = {}) {
  const assetType = String(asset?.assetType || '').trim();
  const assetId = String(asset?.assetId || '').trim();
  if (!assetId) {
    throw new Error('Managed asset path requires assetId.');
  }
  const basePath = `${managementCollectionPathForAssetType(assetType)}/${encodeURIComponent(assetId)}`;
  return includeDeleted ? `${basePath}?includeDeleted=true` : basePath;
}

export function buildAssetListPath(assetType, { owner = '', query = '' } = {}) {
  const normalizedAssetType = String(assetType || '').trim();
  if (normalizedAssetType !== 'component' && normalizedAssetType !== 'variant') {
    throw new Error(`Unsupported asset type: ${normalizedAssetType || 'unknown'}`);
  }
  const params = new URLSearchParams();
  const normalizedOwner = String(owner || '').trim();
  const normalizedQuery = String(query || '').trim();
  if (normalizedOwner) {
    params.set('owner', normalizedOwner);
  }
  if (normalizedQuery) {
    params.set('q', normalizedQuery);
  }
  const queryString = params.toString();
  const basePath = normalizedAssetType === 'component' ? '/v1/components' : '/v1/variants';
  return queryString ? `${basePath}?${queryString}` : basePath;
}

function compareAssetSummaries(left, right) {
  const leftUpdatedAt = String(left?.updatedAt || '');
  const rightUpdatedAt = String(right?.updatedAt || '');
  if (leftUpdatedAt !== rightUpdatedAt) {
    return rightUpdatedAt.localeCompare(leftUpdatedAt);
  }
  const leftName = String(left?.name || left?.assetId || '').toLowerCase();
  const rightName = String(right?.name || right?.assetId || '').toLowerCase();
  if (leftName !== rightName) {
    return leftName.localeCompare(rightName);
  }
  return String(left?.assetId || '').localeCompare(String(right?.assetId || ''));
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
  const sessionStillLoadingWithoutData = sessionQuery.isPending && !isLoggedIn;
  const [authMode, setAuthMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false);
  const [registerName, setRegisterName] = useState('');
  const [registerEmail, setRegisterEmail] = useState('');
  const [registerPassword, setRegisterPassword] = useState('');
  const [registerConfirmPassword, setRegisterConfirmPassword] = useState('');
  const [registerPasswordVisible, setRegisterPasswordVisible] = useState(false);
  const [registerConfirmPasswordVisible, setRegisterConfirmPasswordVisible] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');
  const [currentUser, setCurrentUser] = useState(null);
  const [activePage, setActivePage] = useState('profile');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('Ready');
  const [profileLoadError, setProfileLoadError] = useState('');
  const [sessionPendingTimedOut, setSessionPendingTimedOut] = useState(false);
  const [profilePendingTimedOut, setProfilePendingTimedOut] = useState(false);
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const [siteSettings, setSiteSettings] = useState({
    allowUserRegistration: false,
  });
  const [linkedAccounts, setLinkedAccounts] = useState([]);
  const [profileName, setProfileName] = useState('');
  const [profileEmail, setProfileEmail] = useState('');
  const [mineAssetType, setMineAssetType] = useState('all');
  const [mineQuery, setMineQuery] = useState('');
  const [mineAssets, setMineAssets] = useState([]);

  const [allAssetType, setAllAssetType] = useState('all');
  const [allQuery, setAllQuery] = useState('');
  const [allAssets, setAllAssets] = useState([]);

  const [users, setUsers] = useState([]);
  const [userQuery, setUserQuery] = useState('');
  const [editingUserId, setEditingUserId] = useState('');
  const [editName, setEditName] = useState('');
  const [editRole, setEditRole] = useState('user');
  const [newName, setNewName] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('user');

  const [currentPassword, setCurrentPassword] = useState('');
  const [nextPassword, setNextPassword] = useState('');
  const [confirmNextPassword, setConfirmNextPassword] = useState('');
  const [currentPasswordVisible, setCurrentPasswordVisible] = useState(false);
  const [nextPasswordVisible, setNextPasswordVisible] = useState(false);
  const [confirmNextPasswordVisible, setConfirmNextPasswordVisible] = useState(false);
  const profileStillLoadingWithoutData = isLoggedIn && currentUser === null && !profileLoadError;
  const currentProfileName = String(currentUser?.name || '').trim();
  const editedProfileName = profileName.trim();
  const profileNameChanged = editedProfileName !== currentProfileName;
  const currentProfileEmail = String(currentUser?.email || '').trim().toLowerCase();
  const editedProfileEmail = profileEmail.trim().toLowerCase();
  const profileEmailChanged = editedProfileEmail !== currentProfileEmail;

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [providersResponse, siteSettingsResponse] = await Promise.all([
          fetch('/v1/auth/providers'),
          fetch('/v1/site-settings'),
        ]);
        const data = await parseJsonResponse(providersResponse);
        const siteSettingsData = await parseJsonResponse(siteSettingsResponse);
        if (active) {
          setGoogleEnabled(Boolean(data.google));
          setSiteSettings({
            allowUserRegistration: Boolean(siteSettingsData.allowUserRegistration),
          });
        }
      } catch (error) {
        if (active) {
          setGoogleEnabled(false);
          setSiteSettings({
            allowUserRegistration: false,
          });
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setProfileName(String(currentUser?.name || ''));
  }, [currentUser?.name]);

  useEffect(() => {
    setProfileEmail(String(currentUser?.email || ''));
  }, [currentUser?.email]);

  useEffect(() => {
    if (!isLoggedIn) {
      setCurrentUser(null);
      setProfileLoadError('');
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
    if (!sessionQuery.isPending) {
      setSessionPendingTimedOut(false);
      return undefined;
    }
    const timeoutId = window.setTimeout(() => {
      setSessionPendingTimedOut(true);
    }, 5000);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [sessionQuery.isPending]);

  useEffect(() => {
    if (!profileStillLoadingWithoutData) {
      setProfilePendingTimedOut(false);
      return undefined;
    }
    const timeoutId = window.setTimeout(() => {
      setProfilePendingTimedOut(true);
    }, 5000);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [profileStillLoadingWithoutData]);

  useEffect(() => {
    if (authMode === 'register' && !siteSettings.allowUserRegistration) {
      setAuthMode('login');
    }
  }, [authMode, siteSettings.allowUserRegistration]);

  async function apiRequest(path, options = {}) {
    const { timeoutMs, ...requestOptions } = options;
    const headers = {};
    if (requestOptions.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    let timeoutId = null;
    let timeoutController = null;
    try {
      if (typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0) {
        timeoutController = new AbortController();
        timeoutId = window.setTimeout(() => {
          timeoutController.abort();
        }, timeoutMs);
      }
      const response = await fetch(path, {
        ...requestOptions,
        headers,
        signal: timeoutController ? timeoutController.signal : requestOptions.signal,
      });
      const data = await parseJsonResponse(response);
      if (!response.ok) {
        throw new Error(data.message || `Request failed (${response.status})`);
      }
      return data;
    } catch (error) {
      if (timeoutController && timeoutController.signal.aborted) {
        throw new Error('Request timed out. Please try again.');
      }
      throw error;
    } finally {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    }
  }

  async function loadProfile() {
    const me = await apiRequest('/v1/me', { timeoutMs: 10000 });
    setCurrentUser(me);
    setProfileLoadError('');
    setProfilePendingTimedOut(false);
  }

  async function loadLinkedAccounts() {
    const accounts = await apiRequest('/api/auth/list-accounts', { timeoutMs: 10000 });
    setLinkedAccounts(Array.isArray(accounts) ? accounts : []);
  }

  async function loadSiteSettings() {
    const settings = await apiRequest('/v1/site-settings');
    setSiteSettings({
      allowUserRegistration: Boolean(settings.allowUserRegistration),
    });
  }

  async function loadMineAssets() {
    const result = currentUser?.isAdmin
      ? await loadManagedAssetResults({
          assetType: mineAssetType,
          ownerUserId: currentUser.userId,
          query: mineQuery,
          includeDeleted: true,
        })
      : await loadPublicAssetResults({
          assetType: mineAssetType,
          owner: 'me',
          query: mineQuery,
        });
    setMineAssets(result);
  }

  async function loadAllAssets() {
    const result = currentUser?.isAdmin
      ? await loadManagedAssetResults({
          assetType: allAssetType,
          query: allQuery,
          includeDeleted: true,
        })
      : await loadPublicAssetResults({
          assetType: allAssetType,
          owner: 'public',
          query: allQuery,
        });
    setAllAssets(result);
  }

  async function loadManagedAssetResults({ assetType, ownerUserId = '', query, includeDeleted = false }) {
    const normalizedType = String(assetType || '').trim() || 'all';
    const assetTypes = normalizedType === 'all' ? ASSET_TYPE_OPTIONS : [normalizedType];
    const results = await Promise.all(assetTypes.map(async (type) => {
      const response = await apiRequest(
        buildManagedAssetListPath(type, {
          ownerUserId,
          query,
          includeDeleted,
        }),
      );
      return Array.isArray(response.entries) ? response.entries : [];
    }));
    return results.flat().sort(compareAssetSummaries);
  }

  async function loadPublicAssetResults({ assetType, owner, query }) {
    const normalizedType = String(assetType || '').trim() || 'all';
    const normalizedOwner = String(owner || '').trim();
    const normalizedQuery = String(query || '').trim();
    const assetTypes = normalizedType === 'all' ? ['variant', 'component'] : [normalizedType];
    const results = await Promise.all(assetTypes.map(async (type) => {
      const response = await apiRequest(
        buildAssetListPath(type, {
          owner: normalizedOwner,
          query: normalizedQuery,
        }),
      );
      return Array.isArray(response.entries) ? response.entries : [];
    }));
    return results
      .flat()
      .sort(compareAssetSummaries);
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
        await Promise.all([loadUsers(), loadSiteSettings()]);
      }
      setStatusText('Loaded');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (activePage === 'profile') {
        setProfileLoadError(message);
      }
      setStatusText(message);
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
    if (!email.trim() || !password) {
      setStatusText('Email and password are required.');
      return;
    }
    setLoading(true);
    setStatusText('Signing in...');
    setProfileLoadError('');
    try {
      await authClient.signIn.email({
        email: email.trim(),
        password,
      });
      await sessionQuery.refetch();
      await Promise.all([loadProfile(), loadLinkedAccounts()]);
      setPassword('');
      setActivePage('profile');
      setStatusText(`Signed in as ${email.trim()}`);
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
        callbackURL: CONSOLE_CALLBACK_PATH,
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
          callbackURL: CONSOLE_CALLBACK_PATH,
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
    if (!siteSettings.allowUserRegistration) {
      setStatusText('New user registration is disabled.');
      setAuthMode('login');
      return;
    }
    if (!registerName.trim() || !registerEmail.trim() || !registerPassword) {
      setStatusText('Name, email, and password are required.');
      return;
    }
    if (registerPassword !== registerConfirmPassword) {
      setStatusText('Registration passwords do not match.');
      return;
    }
    setLoading(true);
    setStatusText('Creating account...');
    try {
      await authClient.signUp.email({
        name: registerName.trim(),
        email: registerEmail.trim(),
        password: registerPassword,
        callbackURL: `${window.location.origin}${VERIFY_EMAIL_PATH}?verified=1`,
      });
      setRegisterName('');
      setRegisterEmail('');
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
      setProfileLoadError('');
      setProfilePendingTimedOut(false);
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

  async function onRetryProfileLoad() {
    setLoading(true);
    setProfilePendingTimedOut(false);
    setProfileLoadError('');
    setStatusText('Retrying profile load...');
    try {
      await sessionQuery.refetch();
      await Promise.all([loadProfile(), loadLinkedAccounts()]);
      setStatusText('Loaded');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setProfileLoadError(message);
      setStatusText(message);
    } finally {
      setLoading(false);
    }
  }

  async function onRetrySessionLoad() {
    setSessionPendingTimedOut(false);
    setStatusText('Retrying session load...');
    try {
      await sessionQuery.refetch();
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
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

  async function onUpdateProfileName(event) {
    event.preventDefault();
    if (!currentUser) {
      setStatusText('Profile is not loaded yet.');
      return;
    }
    if (!editedProfileName) {
      setStatusText('Name is required.');
      return;
    }
    if (!profileNameChanged) {
      setStatusText('No profile changes to save.');
      return;
    }
    setLoading(true);
    setStatusText('Updating name...');
    try {
      const updatedUser = await apiRequest('/v1/me', {
        method: 'PUT',
        body: JSON.stringify({ name: editedProfileName }),
      });
      setCurrentUser(updatedUser);
      setProfileName(String(updatedUser?.name || ''));
      await sessionQuery.refetch();
      setStatusText('Name updated');
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (message === 'name already in use' || message === 'duplicate resource') {
        setStatusText('Name is already in use. Choose a different name.');
        return;
      }
      setStatusText(message);
    } finally {
      setLoading(false);
    }
  }

  async function onChangeEmail(event) {
    event.preventDefault();
    if (!currentUser) {
      setStatusText('Profile is not loaded yet.');
      return;
    }
    if (!editedProfileEmail) {
      setStatusText('Email is required.');
      return;
    }
    if (!profileEmailChanged) {
      setStatusText('No email changes to submit.');
      return;
    }
    setLoading(true);
    setStatusText('Submitting email change...');
    try {
      await apiRequest('/api/auth/change-email', {
        method: 'POST',
        body: JSON.stringify({
          newEmail: editedProfileEmail,
          callbackURL: `${window.location.origin}${VERIFY_EMAIL_PATH}?verified=1`,
        }),
      });
      setProfileEmail(String(currentUser.email || ''));
      await Promise.all([sessionQuery.refetch(), loadProfile(), loadLinkedAccounts()]);
      setStatusText(`If ${editedProfileEmail} is available, a verification link has been sent there.`);
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
      const endpoint = currentUser?.isAdmin
        ? buildManagedAssetDetailPath(asset, { includeDeleted: true })
        : asset.assetType === 'variant'
          ? `/v1/variants/${encodeURIComponent(asset.assetId)}`
          : `/v1/components/${encodeURIComponent(asset.assetId)}`;
      await apiRequest(endpoint, { method: 'DELETE' });
      await refreshCurrentPage();
      setStatusText(`Deleted ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onDownloadAsset(asset) {
    try {
      const endpoint = currentUser?.isAdmin
        ? buildManagedAssetDetailPath(asset, { includeDeleted: Boolean(asset.deletedAt) })
        : asset.assetType === 'variant'
          ? `/v1/variants/${encodeURIComponent(asset.assetId)}/content`
          : `/v1/components/${encodeURIComponent(asset.assetId)}/content`;
      const payload = await apiRequest(endpoint);
      const downloadable = downloadableContentForAsset(asset, payload);
      downloadJson(downloadable.filename, downloadable.data);
      setStatusText(`Downloaded content for ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    }
  }

  async function onRestoreAsset(asset) {
    if (!currentUser?.isAdmin) {
      return;
    }
    setLoading(true);
    try {
      await apiRequest(buildManagedAssetDetailPath(asset, { includeDeleted: true }), {
        method: 'PUT',
        body: JSON.stringify({ restore: true }),
      });
      await refreshCurrentPage();
      setStatusText(`Restored ${asset.assetId}`);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
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
          name: newName.trim(),
          email: newEmail.trim(),
          password: newPassword,
          role: newRole,
        }),
      });
      setNewName('');
      setNewEmail('');
      setNewPassword('');
      setNewRole('user');
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
    setEditName(user.name || '');
    setEditRole(String(user.role || 'user'));
  }

  function onCancelEditUser() {
    setEditingUserId('');
    setEditName('');
    setEditRole('user');
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
          name: editName.trim(),
          role: editRole,
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

  async function onSaveSiteSettings(event) {
    event.preventDefault();
    if (!currentUser?.isAdmin) {
      return;
    }
    setLoading(true);
    try {
      const updated = await apiRequest(`${MANAGEMENT_API_BASE_PATH}/site-settings`, {
        method: 'PUT',
        body: JSON.stringify({
          allowUserRegistration: Boolean(siteSettings.allowUserRegistration),
        }),
      });
      setSiteSettings({
        allowUserRegistration: Boolean(updated.allowUserRegistration),
      });
      setStatusText('Site settings updated');
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }

  async function onPurgeAllAssets() {
    if (!currentUser?.isAdmin) {
      return;
    }
    const confirmed = window.confirm(
      'This will permanently delete every asset, every revision, and every subscription. This cannot be undone. Continue?',
    );
    if (!confirmed) {
      return;
    }
    const confirmationText = window.prompt(
      `Type ${PURGE_ALL_ASSETS_CONFIRMATION_TEXT} to permanently delete all assets.`,
      '',
    );
    if (confirmationText === null) {
      setStatusText('Asset purge cancelled');
      return;
    }
    if (confirmationText !== PURGE_ALL_ASSETS_CONFIRMATION_TEXT) {
      setStatusText('Asset purge cancelled: confirmation text did not match');
      return;
    }
    setLoading(true);
    setStatusText('Purging all assets...');
    try {
      const result = await apiRequest(`${MANAGEMENT_API_BASE_PATH}/assets/purge-all`, {
        method: 'POST',
        body: JSON.stringify({ confirmationText }),
      });
      await Promise.all([loadUsers(), loadMineAssets(), loadAllAssets()]);
      setStatusText(
        `Purged ${result.deletedAssets || 0} assets, ${result.deletedAssetVersions || 0} versions, and ${result.deletedAssetSubscriptions || 0} subscriptions`,
      );
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
  const totalUsers = users.length;
  const adminUsers = users.filter((user) => String(user.role || '').toLowerCase() === 'admin').length;
  const readonlyUsers = users.filter((user) => String(user.role || '').toLowerCase() === 'readonly').length;

  if ((sessionStillLoadingWithoutData && !sessionPendingTimedOut) || (profileStillLoadingWithoutData && !profilePendingTimedOut)) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="status">Loading session...</p>
        </div>
      </div>
    );
  }

  if (sessionStillLoadingWithoutData && sessionPendingTimedOut) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="muted">Session loading is taking longer than expected.</p>
          <div className="inline-form">
            <button type="button" onClick={() => void onRetrySessionLoad()} disabled={loading}>Retry Session Load</button>
            <button
              type="button"
              onClick={() => {
                window.location.reload();
              }}
              disabled={loading}
            >
              Reload Page
            </button>
          </div>
          <p className="status">{statusText}</p>
        </div>
      </div>
    );
  }

  if (profileStillLoadingWithoutData && profilePendingTimedOut) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="muted">Your session was found, but loading your profile is taking longer than expected.</p>
          <div className="inline-form">
            <button type="button" onClick={() => void onRetryProfileLoad()} disabled={loading}>Retry Profile Load</button>
            <button type="button" onClick={() => void onLogout()} disabled={loading}>Sign Out</button>
            <button
              type="button"
              onClick={() => {
                window.location.reload();
              }}
              disabled={loading}
            >
              Reload Page
            </button>
          </div>
          <p className="status">{statusText}</p>
        </div>
      </div>
    );
  }

  if (isLoggedIn && currentUser === null && profileLoadError) {
    return (
      <div className="shell login-shell">
        <div className="card panel login-card">
          <h1>Feel8 Management System</h1>
          <p className="muted">Your session exists, but the profile could not be loaded.</p>
          <div className="inline-form">
            <button type="button" onClick={() => void onRetryProfileLoad()} disabled={loading}>Retry Profile Load</button>
            <button type="button" onClick={() => void onLogout()} disabled={loading}>Sign Out</button>
          </div>
          <p className="status">{statusText}</p>
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
          {!siteSettings.allowUserRegistration ? (
            <p className="muted social-hint">New account registration is currently disabled.</p>
          ) : null}
          {googleEnabled && siteSettings.allowUserRegistration ? (
            <p className="muted social-hint">
              Google sign-in is available for direct login and for linking to an existing account.
            </p>
          ) : null}
          <p className="muted field-note">Fields marked * are required.</p>
          <div className="auth-switch">
            <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>Sign In</button>
            {siteSettings.allowUserRegistration ? (
              <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>Register</button>
            ) : null}
            <button type="button" className={authMode === 'forgot' ? 'active' : ''} onClick={() => setAuthMode('forgot')}>Forgot Password</button>
          </div>
          <form onSubmit={authMode === 'login' ? onLogin : authMode === 'register' ? onRegister : onForgotPassword} className="form">
            {authMode === 'login' ? (
              <>
                <label>
                  Email *
                  <input
                    autoComplete="email"
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@example.com"
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
                  Name *
                  <input
                    value={registerName}
                    onChange={(event) => setRegisterName(event.target.value)}
                    placeholder="Your name"
                    required
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
                <FieldHint>Name must be unique. It can include normal display text, including Chinese.</FieldHint>
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
                  !registerName.trim()
                  || !registerEmail.trim()
                  || !registerPassword
                  || !registerConfirmPassword
                  || registerPassword !== registerConfirmPassword
                ))
              }
            >
              {authMode === 'login' ? 'Sign In' : null}
              {authMode === 'register' ? 'Create Account' : null}
              {authMode === 'forgot' ? 'Send Reset Link' : null}
            </button>
          </form>
          {googleEnabled && siteSettings.allowUserRegistration && authMode === 'login' ? (
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
          <p className="muted">Welcome, {currentUser?.name || currentUser?.email}</p>
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
              <div className="profile-editor">
                <div className="section-head">
                  <div>
                    <h3>Identity</h3>
                    <p className="muted">Update your public author name and account email without leaving the console.</p>
                  </div>
                </div>
                <form onSubmit={onUpdateProfileName} className="profile-inline-form">
                  <label className="profile-inline-label">
                    <span>Name</span>
                    <input
                      value={profileName}
                      onChange={(event) => setProfileName(event.target.value)}
                      autoComplete="nickname"
                    />
                  </label>
                  <button type="submit" disabled={loading || !editedProfileName || !profileNameChanged}>
                    Save Name
                  </button>
                </form>
                <p className="muted profile-inline-note">Your public author name is shown on assets you publish.</p>
                <form onSubmit={onChangeEmail} className="profile-inline-form">
                  <label className="profile-inline-label">
                    <span>Email</span>
                    <input
                      autoComplete="email"
                      type="email"
                      value={profileEmail}
                      onChange={(event) => setProfileEmail(event.target.value)}
                    />
                  </label>
                  <button type="submit" disabled={loading || !editedProfileEmail || !profileEmailChanged}>
                    Change Email
                  </button>
                </form>
                <p className="muted profile-inline-note">
                  Changing email sends a verification link to the new address before the change is finalized.
                </p>
              </div>
              <div className="account-card">
                <div className="account-card-head">
                  <div>
                    <h3>Account Overview</h3>
                    <p className="muted">Your current account identity and access status.</p>
                  </div>
                  <div className="account-card-badges">
                    <EmailStatusBadge verified={Boolean(currentUser?.emailVerified)} />
                    <RoleBadge role={currentUser?.role} />
                  </div>
                </div>
                <div className="account-meta-grid">
                  <div className="account-meta-item">
                    <span className="account-meta-label">Name</span>
                    <strong>{currentUser?.name || 'Unknown user'}</strong>
                  </div>
                  <div className="account-meta-item">
                    <span className="account-meta-label">Email</span>
                    <strong>{currentUser?.email || 'No email'}</strong>
                  </div>
                  <div className="account-meta-item">
                    <span className="account-meta-label">Verification</span>
                    <EmailStatusBadge verified={Boolean(currentUser?.emailVerified)} />
                  </div>
                  <div className="account-meta-item">
                    <span className="account-meta-label">Role</span>
                    <RoleBadge role={currentUser?.role} />
                  </div>
                </div>
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
                    placeholder={hasManagementAccess ? 'Search my managed assets' : 'Search my assets'}
                  />
                  <select value={mineAssetType} onChange={(event) => setMineAssetType(event.target.value)}>
                    <option value="all">all</option>
                    <option value="variant">variant</option>
                    <option value="component">component</option>
                  </select>
                  <button type="button" onClick={() => void loadMineAssets()} disabled={loading}>Search</button>
                </div>
              </div>
              <SimpleTable
                columns={hasManagementAccess ? ['Asset ID', 'Type', 'Visibility', 'Revision', 'Deleted', 'Actions'] : ['Asset ID', 'Type', 'Visibility', 'Revision', 'Actions']}
                rows={mineAssets.map((asset) => [
                  asset.assetId,
                  asset.assetType,
                  asset.visibility,
                  asset.revision,
                  ...(hasManagementAccess ? [
                    asset.deletedAt ? (
                      <span title={formatTimestampTooltip(asset.deletedAt)}>
                        {formatTimestampForDisplay(asset.deletedAt)}
                      </span>
                    ) : 'No'
                  ] : []),
                  <div className="inline-form" key={`${asset.assetId}-actions`}>
                    <button type="button" onClick={() => void onDownloadAsset(asset)} disabled={loading}>Download Content</button>
                    {asset.deletedAt ? (
                      <button type="button" className="button-secondary" onClick={() => void onRestoreAsset(asset)} disabled={loading || !hasManagementAccess}>Restore</button>
                    ) : (
                      <button type="button" onClick={() => void onDeleteOwnAsset(asset)} disabled={loading}>Delete</button>
                    )}
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
                    placeholder={hasManagementAccess ? 'Search all managed assets' : 'Search public assets'}
                  />
                  <select value={allAssetType} onChange={(event) => setAllAssetType(event.target.value)}>
                    <option value="all">all</option>
                    <option value="variant">variant</option>
                    <option value="component">component</option>
                  </select>
                  <button type="button" onClick={() => void loadAllAssets()} disabled={loading}>Search</button>
                </div>
              </div>
              <SimpleTable
                columns={hasManagementAccess ? ['Asset ID', 'Type', 'Owner', 'Visibility', 'Deleted', 'Actions'] : ['Asset ID', 'Type', 'Owner', 'Subscribed', 'Actions']}
                rows={allAssets.map((asset) => [
                  asset.assetId,
                  asset.assetType,
                  asset.ownerDisplayName || asset.ownerUserId,
                  ...(hasManagementAccess
                    ? [
                        asset.visibility,
                        asset.deletedAt ? (
                          <span title={formatTimestampTooltip(asset.deletedAt)}>
                            {formatTimestampForDisplay(asset.deletedAt)}
                          </span>
                        ) : 'No',
                        <div className="inline-form" key={`${asset.assetId}-manage`}>
                          <button type="button" onClick={() => void onDownloadAsset(asset)} disabled={loading}>Download Content</button>
                          {asset.deletedAt ? (
                            <button type="button" className="button-secondary" onClick={() => void onRestoreAsset(asset)} disabled={loading}>Restore</button>
                          ) : (
                            <button type="button" className="button-danger" onClick={() => void onDeleteOwnAsset(asset)} disabled={loading}>Delete</button>
                          )}
                        </div>,
                      ]
                    : [
                        asset.subscribed ? 'Yes' : 'No',
                        <button
                          type="button"
                          key={`${asset.assetId}-sub`}
                          onClick={() => void onToggleSubscribe(asset)}
                          disabled={loading}
                        >
                          {asset.subscribed ? 'Unsubscribe' : 'Subscribe'}
                        </button>,
                      ]),
                ])}
                emptyText="No assets"
              />
            </section>
          ) : null}

          {activePage === 'users' && hasManagementAccess ? (
            <section className="card panel management-panel">
              <div className="section-head management-head">
                <div>
                  <h2>User Management</h2>
                  <p className="muted management-subtitle">Manage onboarding, permissions, and account roles from a single workspace.</p>
                </div>
                <div className="management-search">
                  <input
                    value={userQuery}
                    onChange={(event) => setUserQuery(event.target.value)}
                    placeholder="Search by name or email"
                  />
                  <button type="button" onClick={() => void loadUsers()} disabled={loading}>Search</button>
                </div>
              </div>

              <div className="management-stats">
                <div className="management-stat-card">
                  <span className="management-stat-label">Visible Users</span>
                  <strong>{totalUsers}</strong>
                </div>
                <div className="management-stat-card">
                  <span className="management-stat-label">Admins</span>
                  <strong>{adminUsers}</strong>
                </div>
                <div className="management-stat-card">
                  <span className="management-stat-label">Read Only</span>
                  <strong>{readonlyUsers}</strong>
                </div>
              </div>

              <div className="management-control-grid">
                <div className="management-form-stack">
                  <form onSubmit={onSaveSiteSettings} className="form management-card management-settings-card">
                    <div className="management-card-head">
                      <div>
                        <span className="eyebrow">Site Settings</span>
                        <h3>Registration Access</h3>
                      </div>
                    </div>
                    <label className="toggle-card">
                      <div>
                        <span className="toggle-title">Allow new user registration</span>
                        <span className="toggle-description">Let visitors create their own account without an admin invite.</span>
                      </div>
                      <span className="toggle-switch">
                        <input
                          type="checkbox"
                          checked={siteSettings.allowUserRegistration}
                          onChange={(event) => setSiteSettings((current) => ({
                            ...current,
                            allowUserRegistration: event.target.checked,
                          }))}
                        />
                        <span className="toggle-slider" aria-hidden="true" />
                      </span>
                    </label>
                    <div className="management-actions">
                      <button type="submit" disabled={loading}>Save Site Settings</button>
                    </div>
                  </form>

                  <section className="management-card management-card-danger">
                    <div className="management-card-head">
                      <div>
                        <span className="eyebrow">Danger Zone</span>
                        <h3>Purge All Assets</h3>
                      </div>
                    </div>
                    <p className="muted management-danger-copy">
                      Permanently delete every component, variant, revision history, and subscription in the asset cloud.
                      This is a hard delete and cannot be restored.
                    </p>
                    <div className="management-actions">
                      <button type="button" className="button-danger" onClick={() => void onPurgeAllAssets()} disabled={loading}>
                        Purge All Assets
                      </button>
                    </div>
                  </section>
                </div>

                <div className="management-form-stack">
                  <form onSubmit={onCreateUser} className="form management-card">
                    <div className="management-card-head">
                      <div>
                        <span className="eyebrow">Create User</span>
                        <h3>Invite or Provision an Account</h3>
                      </div>
                      <RoleBadge role={newRole} />
                    </div>
                    <div className="form-grid form-grid-2">
                      <label>
                        Name
                        <input
                          value={newName}
                          onChange={(event) => setNewName(event.target.value)}
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
                      <label className="field-span-2">
                        Role
                        <select value={newRole} onChange={(event) => setNewRole(event.target.value)}>
                          {USER_ROLE_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </select>
                      </label>
                    </div>
                    <div className="management-actions">
                      <button type="submit" disabled={loading || !newName.trim() || !newEmail.trim() || !newPassword}>Create User</button>
                    </div>
                  </form>

                  {editingUserId ? (
                    <form onSubmit={onUpdateUser} className="form management-card management-card-accent">
                      <div className="management-card-head">
                        <div>
                          <span className="eyebrow">Edit User</span>
                          <h3>Update Existing Account</h3>
                        </div>
                        <RoleBadge role={editRole} />
                      </div>
                      <div className="form-grid form-grid-2">
                        <label>
                          Name
                          <input
                            value={editName}
                            onChange={(event) => setEditName(event.target.value)}
                          />
                        </label>
                        <label className="field-span-2">
                          Role
                          <select value={editRole} onChange={(event) => setEditRole(event.target.value)}>
                            {USER_ROLE_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <div className="management-actions">
                        <button type="submit" disabled={loading || !editName.trim()}>Save Changes</button>
                        <button type="button" className="button-secondary" onClick={onCancelEditUser} disabled={loading}>Cancel</button>
                      </div>
                    </form>
                  ) : null}
                </div>
              </div>

              <div className="management-table-card">
                <div className="section-head management-table-head">
                  <div>
                    <h3>User Directory</h3>
                    <p className="muted management-subtitle">Review account roles, activity footprint, and moderation actions.</p>
                  </div>
                </div>
                <SimpleTable
                columns={['Name', 'Email', 'Role', 'Assets', 'Created At', 'Actions']}
                rows={users.map((user) => [
                  <div className="table-primary-cell" key={`${user.userId}-name`}>
                    <strong>{user.name || user.email || 'Unknown user'}</strong>
                  </div>,
                  <span className="muted" key={`${user.userId}-email`}>{user.email || 'No email'}</span>,
                  <RoleBadge role={user.role} key={`${user.userId}-role`} />,
                  <span className="asset-count-pill" key={`${user.userId}-assets`}>{String(user.assetCount || 0)} assets</span>,
                  <span
                    className="muted"
                    key={`${user.userId}-created`}
                    title={formatTimestampTooltip(user.createdAt)}
                  >
                    {formatTimestampForDisplay(user.createdAt)}
                  </span>,
                  <div className="inline-form table-actions" key={`${user.userId}-actions`}>
                    <button
                      type="button"
                      className="button-secondary"
                      onClick={() => onBeginEditUser(user)}
                      disabled={loading}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="button-danger"
                      onClick={() => void onDeleteUser(user.userId, user.name || user.email)}
                      disabled={loading}
                    >
                      Delete
                    </button>
                  </div>,
                ])}
                emptyText="No users"
                />
              </div>
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
      return 'Email verified. You can continue in the console.';
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
      setStatusText('Email verified. You can continue in the console.');
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

function formatUserRole(role) {
  const normalizedRole = String(role || 'user').trim().toLowerCase();
  if (normalizedRole === 'admin') {
    return 'Admin';
  }
  if (normalizedRole === 'readonly') {
    return 'Read Only';
  }
  return 'User';
}

function RoleBadge({ role }) {
  const normalizedRole = String(role || 'user').trim().toLowerCase();
  const className = normalizedRole === 'admin'
    ? 'role-badge role-badge-admin'
    : normalizedRole === 'readonly'
      ? 'role-badge role-badge-readonly'
      : 'role-badge role-badge-user';
  return <span className={className}>{formatUserRole(role)}</span>;
}

function EmailStatusBadge({ verified }) {
  const className = verified
    ? 'status-pill status-pill-verified'
    : 'status-pill status-pill-pending';
  return <span className={className}>{verified ? 'Email Verified' : 'Email Unverified'}</span>;
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
