const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

export async function hashPassword(password) {
  const iterations = 100000;
  const salt = randomToken(16);
  const hash = await derivePbkdf2Hash(password, salt, iterations);
  return `pbkdf2_sha256$${iterations}$${salt}$${hash}`;
}

export async function verifyPassword(password, encodedHash) {
  const parts = String(encodedHash || '').split('$');
  if (parts.length !== 4) {
    return false;
  }
  const [algorithm, iterationsText, salt, expectedHash] = parts;
  if (algorithm !== 'pbkdf2_sha256') {
    return false;
  }
  const iterations = Number.parseInt(iterationsText, 10);
  if (!Number.isFinite(iterations) || iterations <= 0) {
    return false;
  }
  const actualHash = await derivePbkdf2Hash(password, salt, iterations);
  return timingSafeEqual(actualHash, expectedHash);
}

export async function issueJwt({ secret, issuer, subject, tokenType, ttlSeconds, tokenId }) {
  const now = nowEpochSeconds();
  const header = { alg: 'HS256', typ: 'JWT' };
  const payload = {
    iss: issuer,
    sub: subject,
    typ: tokenType,
    iat: now,
    exp: now + ttlSeconds,
  };
  if (tokenId) {
    payload.jti = tokenId;
  }
  return signJwt(header, payload, secret);
}

export async function verifyJwt({ token, secret, issuer, expectedType }) {
  const parts = String(token || '').split('.');
  if (parts.length !== 3) {
    throw new Error('invalid token');
  }
  const [encodedHeader, encodedPayload, encodedSignature] = parts;
  const payloadText = base64UrlDecodeToText(encodedPayload);
  const payload = JSON.parse(payloadText);
  const signatureInput = `${encodedHeader}.${encodedPayload}`;
  const key = await importHmacKey(secret);
  const valid = await crypto.subtle.verify(
    'HMAC',
    key,
    base64UrlDecodeToBytes(encodedSignature),
    textEncoder.encode(signatureInput),
  );
  if (!valid) {
    throw new Error('invalid token signature');
  }
  if (String(payload.iss || '') !== String(issuer || '')) {
    throw new Error('invalid token issuer');
  }
  if (String(payload.typ || '') !== String(expectedType || '')) {
    throw new Error('invalid token type');
  }
  if (Number(payload.exp || 0) <= nowEpochSeconds()) {
    throw new Error('token expired');
  }
  return payload;
}

export async function issueTokenPair({ secret, issuer, userId, accessTtlSeconds, refreshTtlSeconds, refreshTokenId }) {
  const accessToken = await issueJwt({
    secret,
    issuer,
    subject: userId,
    tokenType: 'access',
    ttlSeconds: accessTtlSeconds,
  });
  const refreshToken = await issueJwt({
    secret,
    issuer,
    subject: userId,
    tokenType: 'refresh',
    ttlSeconds: refreshTtlSeconds,
    tokenId: refreshTokenId,
  });
  return { accessToken, refreshToken };
}

export function nowIso() {
  return new Date().toISOString();
}

export function randomToken(bytes = 16) {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return base64UrlEncodeBytes(value);
}

async function derivePbkdf2Hash(password, salt, iterations) {
  const keyMaterial = await crypto.subtle.importKey(
    'raw',
    textEncoder.encode(String(password || '')),
    'PBKDF2',
    false,
    ['deriveBits'],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: 'PBKDF2',
      hash: 'SHA-256',
      salt: textEncoder.encode(String(salt || '')),
      iterations,
    },
    keyMaterial,
    256,
  );
  return base64UrlEncodeBytes(new Uint8Array(bits));
}

async function signJwt(header, payload, secret) {
  const encodedHeader = base64UrlEncodeText(JSON.stringify(header));
  const encodedPayload = base64UrlEncodeText(JSON.stringify(payload));
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const key = await importHmacKey(secret);
  const signature = await crypto.subtle.sign('HMAC', key, textEncoder.encode(signingInput));
  return `${signingInput}.${base64UrlEncodeBytes(new Uint8Array(signature))}`;
}

async function importHmacKey(secret) {
  return crypto.subtle.importKey(
    'raw',
    textEncoder.encode(String(secret || '')),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  );
}

function nowEpochSeconds() {
  return Math.floor(Date.now() / 1000);
}

function base64UrlEncodeText(value) {
  return base64UrlEncodeBytes(textEncoder.encode(value));
}

function base64UrlDecodeToText(value) {
  return textDecoder.decode(base64UrlDecodeToBytes(value));
}

function base64UrlEncodeBytes(value) {
  return btoa(bytesToBinaryString(value))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function base64UrlDecodeToBytes(value) {
  const normalized = String(value || '')
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const padding = normalized.length % 4 === 0 ? '' : '='.repeat(4 - (normalized.length % 4));
  return binaryStringToBytes(atob(`${normalized}${padding}`));
}

function timingSafeEqual(left, right) {
  const leftBytes = textEncoder.encode(String(left || ''));
  const rightBytes = textEncoder.encode(String(right || ''));
  let mismatch = leftBytes.length ^ rightBytes.length;
  const length = Math.max(leftBytes.length, rightBytes.length);
  for (let index = 0; index < length; index += 1) {
    const leftByte = index < leftBytes.length ? leftBytes[index] : 0;
    const rightByte = index < rightBytes.length ? rightBytes[index] : 0;
    mismatch |= leftByte ^ rightByte;
  }
  return mismatch === 0;
}

function bytesToBinaryString(value) {
  let out = '';
  for (const byte of value) {
    out += String.fromCharCode(byte);
  }
  return out;
}

function binaryStringToBytes(value) {
  const out = new Uint8Array(value.length);
  for (let index = 0; index < value.length; index += 1) {
    out[index] = value.charCodeAt(index);
  }
  return out;
}
