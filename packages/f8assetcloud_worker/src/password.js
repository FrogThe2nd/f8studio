const PASSWORD_HASH_VERSION = 'f8pbkdf2-sha256-v1';
const PBKDF2_ITERATIONS = 50000;
const SALT_BYTES = 16;
const KEY_BYTES = 32;
const textEncoder = new TextEncoder();

export function authPasswordHashVersion() {
  return `${PASSWORD_HASH_VERSION}:${PBKDF2_ITERATIONS}`;
}

export async function hashAuthPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const key = await derivePasswordKey(String(password), salt, PBKDF2_ITERATIONS);
  return [
    PASSWORD_HASH_VERSION,
    String(PBKDF2_ITERATIONS),
    bytesToBase64Url(salt),
    bytesToBase64Url(key),
  ].join('$');
}

export async function verifyAuthPassword({ hash, password }) {
  const parsed = parsePasswordHash(hash);
  if (parsed === null) {
    return false;
  }
  const actualKey = await derivePasswordKey(String(password), parsed.salt, parsed.iterations);
  return timingSafeEqual(actualKey, parsed.key);
}

function parsePasswordHash(hash) {
  const parts = String(hash || '').split('$');
  if (parts.length !== 4) {
    return null;
  }
  const [version, iterationsText, saltText, keyText] = parts;
  if (version !== PASSWORD_HASH_VERSION) {
    return null;
  }
  const iterations = Number(iterationsText);
  if (!Number.isInteger(iterations) || iterations < 10000 || iterations > 1000000) {
    return null;
  }
  const salt = base64UrlToBytes(saltText);
  const key = base64UrlToBytes(keyText);
  if (salt === null || key === null) {
    return null;
  }
  if (salt.length !== SALT_BYTES || key.length !== KEY_BYTES) {
    return null;
  }
  return {
    iterations,
    salt,
    key,
  };
}

async function derivePasswordKey(password, salt, iterations) {
  const importedKey = await crypto.subtle.importKey(
    'raw',
    textEncoder.encode(password.normalize('NFKC')),
    'PBKDF2',
    false,
    ['deriveBits'],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: 'PBKDF2',
      hash: 'SHA-256',
      salt,
      iterations,
    },
    importedKey,
    KEY_BYTES * 8,
  );
  return new Uint8Array(bits);
}

function timingSafeEqual(left, right) {
  if (left.length !== right.length) {
    return false;
  }
  let diff = 0;
  for (let index = 0; index < left.length; index += 1) {
    diff |= left[index] ^ right[index];
  }
  return diff === 0;
}

function bytesToBase64Url(bytes) {
  let binary = '';
  for (const value of bytes) {
    binary += String.fromCharCode(value);
  }
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function base64UrlToBytes(value) {
  const text = String(value || '');
  if (!/^[A-Za-z0-9_-]+$/.test(text) || text.length % 4 === 1) {
    return null;
  }
  const normalized = text.replace(/-/g, '+').replace(/_/g, '/');
  const paddingLength = (4 - (normalized.length % 4)) % 4;
  const padded = normalized + '='.repeat(paddingLength);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}
