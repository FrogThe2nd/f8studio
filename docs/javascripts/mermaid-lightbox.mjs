const LIGHTBOX_ID = "f8-mermaid-lightbox";

function ensureLightbox() {
  const existing = document.getElementById(LIGHTBOX_ID);
  if (existing instanceof HTMLDivElement) {
    return existing;
  }

  const overlay = document.createElement("div");
  overlay.id = LIGHTBOX_ID;
  overlay.className = "f8-mermaid-lightbox";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Mermaid diagram");

  const panel = document.createElement("div");
  panel.className = "f8-mermaid-lightbox__panel";

  const header = document.createElement("div");
  header.className = "f8-mermaid-lightbox__header";

  const title = document.createElement("div");
  title.className = "f8-mermaid-lightbox__title";
  title.textContent = "Mermaid diagram";

  const closeBtn = document.createElement("button");
  closeBtn.className = "f8-mermaid-lightbox__close";
  closeBtn.type = "button";
  closeBtn.textContent = "Close";

  const body = document.createElement("div");
  body.className = "f8-mermaid-lightbox__body";

  header.appendChild(title);
  header.appendChild(closeBtn);
  panel.appendChild(header);
  panel.appendChild(body);
  overlay.appendChild(panel);

  closeBtn.addEventListener("click", () => closeLightbox(overlay));
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      closeLightbox(overlay);
    }
  });

  document.body.appendChild(overlay);
  return overlay;
}

function setScrollLocked(locked) {
  const cls = "f8-mermaid-lightbox-open";
  if (locked) {
    document.documentElement.classList.add(cls);
    document.body.classList.add(cls);
  } else {
    document.documentElement.classList.remove(cls);
    document.body.classList.remove(cls);
  }
}

function closeLightbox(overlay) {
  overlay.classList.remove("f8-mermaid-lightbox--open");
  const body = overlay.querySelector(".f8-mermaid-lightbox__body");
  if (body instanceof HTMLDivElement) {
    body.replaceChildren();
  }
  setScrollLocked(false);
}

function getOrCaptureMermaidSource(container) {
  const existing = String(container.getAttribute("data-f8-mermaid-source") || "").trim();
  if (existing) {
    return existing;
  }
  const code = container.querySelector("code");
  if (!code) {
    return "";
  }
  const src = String(code.textContent || "").trim();
  if (!src) {
    return "";
  }
  container.setAttribute("data-f8-mermaid-source", src);
  return src;
}

function getRenderedSvg(container) {
  const svg = container.querySelector("svg");
  if (!svg) {
    return "";
  }
  try {
    return String(svg.outerHTML || "");
  } catch (_err) {
    return "";
  }
}

function uniqueRenderId() {
  try {
    const buf = new Uint32Array(2);
    crypto.getRandomValues(buf);
    return `f8-mermaid-${buf[0].toString(16)}-${buf[1].toString(16)}`;
  } catch (_err) {
    return `f8-mermaid-${Date.now()}`;
  }
}

async function renderMermaidSvg(source) {
  const m = window.mermaid;
  if (!m || typeof m.render !== "function") {
    return "";
  }
  const id = uniqueRenderId();
  try {
    const res = await m.render(id, source);
    if (typeof res === "string") {
      return res;
    }
    if (res && typeof res.svg === "string") {
      return res.svg;
    }
    return "";
  } catch (_err) {
    return "";
  }
}

async function openLightboxFromMermaid(container) {
  const overlay = ensureLightbox();
  const body = overlay.querySelector(".f8-mermaid-lightbox__body");
  if (!(body instanceof HTMLDivElement)) {
    return;
  }

  const src = getOrCaptureMermaidSource(container);
  let svgHtml = src ? await renderMermaidSvg(src) : "";
  if (!svgHtml) {
    svgHtml = getRenderedSvg(container);
  }
  if (!svgHtml) {
    return;
  }

  body.innerHTML = svgHtml;
  overlay.classList.add("f8-mermaid-lightbox--open");
  setScrollLocked(true);

  const closeBtn = overlay.querySelector(".f8-mermaid-lightbox__close");
  if (closeBtn instanceof HTMLButtonElement) {
    closeBtn.focus();
  }
}

function shouldIgnoreClick(eventTarget, container) {
  if (!(eventTarget instanceof Element)) {
    return true;
  }
  if (eventTarget.closest(`#${LIGHTBOX_ID}`)) {
    return true;
  }
  const link = eventTarget.closest("a");
  if (link && container.contains(link)) {
    return true;
  }
  return false;
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const container = target.closest(".mermaid");
  if (!(container instanceof HTMLElement)) {
    return;
  }
  if (shouldIgnoreClick(target, container)) {
    return;
  }
  void openLightboxFromMermaid(container);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") {
    return;
  }
  const overlay = document.getElementById(LIGHTBOX_ID);
  if (!(overlay instanceof HTMLDivElement)) {
    return;
  }
  if (!overlay.classList.contains("f8-mermaid-lightbox--open")) {
    return;
  }
  closeLightbox(overlay);
});

