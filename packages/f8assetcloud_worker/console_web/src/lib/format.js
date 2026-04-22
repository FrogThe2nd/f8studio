function plainTextFromMarkdown(value) {
  return String(value || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^>\s?/gm, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/\*\*|__|\*|_/g, '')
    .replace(/\r/g, '')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

export function formatTimestamp(value) {
  const text = String(value || '').trim();
  if (!text) {
    return 'Unknown';
  }
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) {
    return text;
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatRelativeVersion(versionNumber) {
  const numberValue = Number(versionNumber);
  return Number.isFinite(numberValue) ? `Version ${numberValue}` : 'Latest';
}

export function summarizeDescription(value) {
  const text = plainTextFromMarkdown(value);
  if (!text) {
    return 'No description provided yet.';
  }
  if (text.length <= 140) {
    return text;
  }
  return `${text.slice(0, 137).trimEnd()}...`;
}
