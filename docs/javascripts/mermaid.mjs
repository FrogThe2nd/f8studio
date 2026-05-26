import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
import elkLayouts from 'https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0/dist/mermaid-layout-elk.esm.min.mjs';

mermaid.registerLayoutLoaders(elkLayouts);
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  layout: "elk",
  flowchart: { useMaxWidth: false },
  sequence: { useMaxWidth: false },
});

// Important: necessary to make it visible to Zensical
window.mermaid = mermaid;

function captureMermaidSources(root) {
  const scope = root instanceof Element ? root : document;
  const blocks = Array.from(scope.querySelectorAll(".mermaid"));
  for (const block of blocks) {
    const existing = String(block.getAttribute("data-f8-mermaid-source") || "");
    if (existing) {
      continue;
    }
    const code = block.querySelector("code");
    const src = String((code ? code.textContent : block.textContent) || "").trim();
    if (!src) {
      continue;
    }
    block.setAttribute("data-f8-mermaid-source", src);
  }
}

function normalizeMermaidBlocks(root) {
  const scope = root instanceof Element ? root : document;
  const blocks = Array.from(scope.querySelectorAll("pre.mermaid"));
  for (const block of blocks) {
    if (!(block instanceof HTMLElement)) {
      continue;
    }
    const code = block.querySelector("code");
    const src = String((code ? code.textContent : block.textContent) || "").trim();
    if (!src) {
      continue;
    }
    const replacement = document.createElement("div");
    replacement.className = "mermaid";
    replacement.setAttribute("data-f8-mermaid-source", src);
    replacement.textContent = src;
    block.replaceWith(replacement);
  }
}

function scheduleMermaidRender(root) {
  const scope = root instanceof Element ? root : document;
  normalizeMermaidBlocks(scope);
  captureMermaidSources(scope);
  try {
    const result = mermaid.run({ querySelector: ".mermaid" });
    if (result && typeof result.catch === "function") {
      result
        .then(() => {
          window.dispatchEvent(new CustomEvent("f8:mermaid-rendered"));
        })
        .catch((err) => {
        console.warn("Mermaid render failed", err);
        });
    }
  } catch (_err) {
    // Best-effort: rendering is handled elsewhere if Mermaid cannot run here.
  }
}

if (window.document$ && typeof window.document$.subscribe === "function") {
  window.document$.subscribe((evt) => {
    scheduleMermaidRender(evt);
  });
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    scheduleMermaidRender(document);
  });
} else {
  scheduleMermaidRender(document);
}
