const LIGHTBOX_ID = "f8-mermaid-lightbox";
const MIN_SCALE = 0.35;
const MAX_SCALE = 8;
const SCALE_STEP = 1.25;

let activeSvg = null;
let activeBaseWidth = 0;
let activeBaseHeight = 0;
let activeScale = 1;

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

  const toolbar = document.createElement("div");
  toolbar.className = "f8-mermaid-lightbox__toolbar";

  const zoomOutBtn = document.createElement("button");
  zoomOutBtn.className = "f8-mermaid-lightbox__button";
  zoomOutBtn.type = "button";
  zoomOutBtn.setAttribute("data-f8-mermaid-action", "zoom-out");
  zoomOutBtn.textContent = "-";

  const zoomLabel = document.createElement("span");
  zoomLabel.className = "f8-mermaid-lightbox__zoom";
  zoomLabel.textContent = "100%";

  const zoomInBtn = document.createElement("button");
  zoomInBtn.className = "f8-mermaid-lightbox__button";
  zoomInBtn.type = "button";
  zoomInBtn.setAttribute("data-f8-mermaid-action", "zoom-in");
  zoomInBtn.textContent = "+";

  const resetBtn = document.createElement("button");
  resetBtn.className = "f8-mermaid-lightbox__button";
  resetBtn.type = "button";
  resetBtn.setAttribute("data-f8-mermaid-action", "reset");
  resetBtn.textContent = "Reset";

  const closeBtn = document.createElement("button");
  closeBtn.className = "f8-mermaid-lightbox__button f8-mermaid-lightbox__close";
  closeBtn.type = "button";
  closeBtn.textContent = "Close";

  const body = document.createElement("div");
  body.className = "f8-mermaid-lightbox__body";

  toolbar.appendChild(zoomOutBtn);
  toolbar.appendChild(zoomLabel);
  toolbar.appendChild(zoomInBtn);
  toolbar.appendChild(resetBtn);
  toolbar.appendChild(closeBtn);
  header.appendChild(title);
  header.appendChild(toolbar);
  panel.appendChild(header);
  panel.appendChild(body);
  overlay.appendChild(panel);

  closeBtn.addEventListener("click", () => closeLightbox(overlay));
  toolbar.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const action = target.getAttribute("data-f8-mermaid-action");
    if (action === "zoom-in") {
      setScale(activeScale * SCALE_STEP);
    } else if (action === "zoom-out") {
      setScale(activeScale / SCALE_STEP);
    } else if (action === "reset") {
      setScale(1);
    }
  });
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
  activeSvg = null;
  activeBaseWidth = 0;
  activeBaseHeight = 0;
  activeScale = 1;
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

function parseSvgLength(value) {
  const text = String(value || "").trim();
  if (!text) {
    return 0;
  }
  const match = text.match(/^([0-9]+(?:\.[0-9]+)?)/);
  if (!match) {
    return 0;
  }
  return Number(match[1]) || 0;
}

function readSvgSize(svg) {
  const viewBox = svg.getAttribute("viewBox");
  if (viewBox) {
    const parts = viewBox.split(/\s+/).map((part) => Number(part));
    if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
      return { width: parts[2], height: parts[3] };
    }
  }

  const attrWidth = parseSvgLength(svg.getAttribute("width"));
  const attrHeight = parseSvgLength(svg.getAttribute("height"));
  if (attrWidth > 0 && attrHeight > 0) {
    return { width: attrWidth, height: attrHeight };
  }

  const rect = svg.getBoundingClientRect();
  return {
    width: Math.max(600, rect.width || 0),
    height: Math.max(360, rect.height || 0),
  };
}

function updateZoomLabel() {
  const overlay = document.getElementById(LIGHTBOX_ID);
  if (!(overlay instanceof HTMLDivElement)) {
    return;
  }
  const label = overlay.querySelector(".f8-mermaid-lightbox__zoom");
  if (label instanceof HTMLSpanElement) {
    label.textContent = `${Math.round(activeScale * 100)}%`;
  }
}

function applySvgScale() {
  if (!(activeSvg instanceof SVGElement)) {
    return;
  }
  activeSvg.style.maxWidth = "none";
  activeSvg.style.maxHeight = "none";
  activeSvg.style.width = `${Math.max(1, activeBaseWidth * activeScale)}px`;
  activeSvg.style.height = `${Math.max(1, activeBaseHeight * activeScale)}px`;
  updateZoomLabel();
}

function setScale(scale) {
  activeScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
  applySvgScale();
}

function fitInitialScale(body, svg) {
  const size = readSvgSize(svg);
  activeSvg = svg;
  activeBaseWidth = size.width;
  activeBaseHeight = size.height;

  const availableWidth = Math.max(320, body.clientWidth - 24);
  const availableHeight = Math.max(240, body.clientHeight - 24);
  const fitScale = Math.min(1, availableWidth / activeBaseWidth, availableHeight / activeBaseHeight);
  activeScale = Math.max(MIN_SCALE, Math.min(1, fitScale || 1));
  applySvgScale();
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
  const svg = body.querySelector("svg");
  if (svg instanceof SVGElement) {
    fitInitialScale(body, svg);
  }

  const closeBtn = overlay.querySelector(".f8-mermaid-lightbox__close");
  if (closeBtn instanceof HTMLButtonElement) {
    closeBtn.focus();
  }
}

function enhanceMermaidContainer(container) {
  if (container.getAttribute("data-f8-mermaid-enhanced") === "true" && container.querySelector(".f8-mermaid-open")) {
    return;
  }
  container.setAttribute("data-f8-mermaid-enhanced", "true");
  container.setAttribute("title", "Click to enlarge Mermaid diagram");

  const button = document.createElement("button");
  button.className = "f8-mermaid-open";
  button.type = "button";
  button.textContent = "Expand";
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    void openLightboxFromMermaid(container);
  });
  container.appendChild(button);
}

function enhanceMermaidBlocks(root) {
  const scope = root instanceof Element ? root : document;
  const containers = Array.from(scope.querySelectorAll(".mermaid"));
  for (const container of containers) {
    if (container instanceof Element) {
      enhanceMermaidContainer(container);
    }
  }
}

function scheduleEnhance(root) {
  window.setTimeout(() => enhanceMermaidBlocks(root), 0);
  window.setTimeout(() => enhanceMermaidBlocks(root), 250);
  window.setTimeout(() => enhanceMermaidBlocks(root), 1000);
}

function observeMermaidBlocks() {
  if (typeof MutationObserver !== "function") {
    return;
  }
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type !== "childList") {
        continue;
      }
      for (const node of record.addedNodes) {
        if (node instanceof Element) {
          const parentMermaid = node.closest(".mermaid");
          if (node.matches(".mermaid") || node.querySelector(".mermaid") || parentMermaid) {
            scheduleEnhance(node);
            if (parentMermaid) {
              scheduleEnhance(parentMermaid);
            }
            return;
          }
        }
      }
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
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

if (window.document$ && typeof window.document$.subscribe === "function") {
  window.document$.subscribe((evt) => {
    scheduleEnhance(evt);
  });
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    scheduleEnhance(document);
  });
} else {
  scheduleEnhance(document);
}

observeMermaidBlocks();

window.addEventListener("f8:mermaid-rendered", () => {
  scheduleEnhance(document);
});
