function normalizePath(pathname) {
  const raw = String(pathname || "").trim();
  if (!raw) {
    return "/";
  }
  if (!raw.startsWith("/")) {
    return `/${raw}`;
  }
  return raw;
}

function toEnglishPath(pathname) {
  const p = normalizePath(pathname);
  if (p === "/zh" || p === "/zh/") {
    return "/";
  }
  if (p.startsWith("/zh/")) {
    return p.slice(3);
  }
  return p;
}

function toChineseCandidate(englishPath) {
  const p = normalizePath(englishPath);
  if (p === "/") {
    return "/zh/";
  }
  return `/zh${p}`;
}

function normalizeLanguageCode(anchor) {
  const hreflang = String(anchor.getAttribute("hreflang") || "").toLowerCase();
  if (hreflang) {
    return hreflang;
  }
  const lang = String(anchor.getAttribute("lang") || "").toLowerCase();
  if (lang) {
    return lang;
  }
  const text = String(anchor.textContent || "").toLowerCase();
  if (text.includes("chinese") || text.includes("中文")) {
    return "zh";
  }
  if (text.includes("english")) {
    return "en";
  }
  return "";
}

async function pathExists(pathname) {
  const path = normalizePath(pathname);
  try {
    const head = await fetch(path, { method: "HEAD" });
    if (head.ok) {
      return true;
    }
  } catch (_err) {
    // Ignore and fallback to GET probe.
  }
  try {
    const get = await fetch(path, { method: "GET" });
    return get.ok;
  } catch (_err) {
    return false;
  }
}

async function rewriteLanguageLinks() {
  const anchors = Array.from(
    document.querySelectorAll(
      "[data-md-component='alternate'] a, a[hreflang], .md-select__list a"
    )
  );
  if (!anchors.length) {
    return;
  }

  const englishPath = toEnglishPath(window.location.pathname);
  const chineseCandidate = toChineseCandidate(englishPath);
  const chinesePath = (await pathExists(chineseCandidate)) ? chineseCandidate : englishPath;

  for (const anchor of anchors) {
    const code = normalizeLanguageCode(anchor);
    if (code.startsWith("zh")) {
      anchor.setAttribute("href", chinesePath);
      continue;
    }
    if (code.startsWith("en")) {
      anchor.setAttribute("href", englishPath);
    }
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    void rewriteLanguageLinks();
  });
} else {
  void rewriteLanguageLinks();
}
