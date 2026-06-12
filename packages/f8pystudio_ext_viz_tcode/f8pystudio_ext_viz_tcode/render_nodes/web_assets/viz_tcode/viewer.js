const modelLabel = document.getElementById("modelLabel");
const stateLabel = document.getElementById("stateLabel");

const OSR_EMULATOR_MODULE_URLS = [
  "https://unpkg.com/osr-emu@0.7.0",
  "https://cdn.jsdelivr.net/npm/osr-emu@0.7.0/+esm",
];

let emulator = null;
let currentModel = "SR6";
let emulatorCtor = null;
let emulatorCtorPromise = null;
let createSequence = 0;
let activeCreateModel = null;
let requestedCreateModel = null;
let createInFlight = false;
let detached = false;
const pendingWrites = [];


function setState(text) {
  if (stateLabel) {
    stateLabel.textContent = `state: ${text}`;
  }
}

function setModelLabel(model) {
  if (modelLabel) {
    modelLabel.textContent = `model: ${model}`;
  }
}

function destroyEmulator() {
  if (emulator && typeof emulator.dispose === "function") {
    try {
      emulator.dispose();
    } catch (_) {
      // ignore dispose errors at UI boundary
    }
  }
  if (emulator && typeof emulator.destroy === "function") {
    try {
      emulator.destroy();
    } catch (_) {
      // ignore destroy errors at UI boundary
    }
  }
  emulator = null;
}

async function loadEmulatorCtor() {
  if (typeof emulatorCtor === "function") {
    return emulatorCtor;
  }
  if (emulatorCtorPromise) {
    return emulatorCtorPromise;
  }
  emulatorCtorPromise = (async () => {
    const errors = [];
    for (const url of OSR_EMULATOR_MODULE_URLS) {
      try {
        const mod = await import(url);
        const ctor = mod && typeof mod.OSREmulator === "function"
          ? mod.OSREmulator
          : mod && typeof mod.default === "function"
            ? mod.default
            : null;
        if (typeof ctor !== "function") {
          throw new Error("OSREmulator export missing");
        }
        emulatorCtor = ctor;
        return ctor;
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        errors.push(`${url} -> ${msg}`);
      }
    }
    throw new Error(errors.join("; "));
  })()
    .catch((error) => {
      emulatorCtorPromise = null;
      throw error;
    });
  return emulatorCtorPromise;
}

function flushPendingWrites() {
  if (!emulator || typeof emulator.write !== "function") {
    return;
  }
  while (pendingWrites.length > 0) {
    const line = pendingWrites.shift();
    if (line === undefined) {
      continue;
    }
    try {
      emulator.write(String(line));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setState(`write error: ${msg}`);
      return;
    }
  }
}

async function createEmulator(model) {
  const sequence = createSequence + 1;
  createSequence = sequence;
  activeCreateModel = model;
  let ctor = null;
  try {
    ctor = await loadEmulatorCtor();
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    setState(`CDN load error: ${msg}`);
    activeCreateModel = null;
    return;
  }
  if (sequence !== createSequence) {
    activeCreateModel = null;
    return;
  }
  destroyEmulator();
  try {
    emulator = new ctor("#canvas", { model });
    if (sequence !== createSequence) {
      destroyEmulator();
      activeCreateModel = null;
      return;
    }
    currentModel = model;
    setModelLabel(model);
    flushPendingWrites();
    setState("ready");
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    setState(`init error: ${msg}`);
  } finally {
    if (activeCreateModel === model) {
      activeCreateModel = null;
    }
  }
}

function normalizeModel(model) {
  const text = String(model || "").toUpperCase();
  if (text === "OSR2" || text === "SR6" || text === "SSR1") {
    return text;
  }
  return "SR6";
}

function desiredCreateModel() {
  return activeCreateModel || requestedCreateModel || currentModel;
}

function requestCreateEmulator(model) {
  const nextModel = normalizeModel(model);
  const wasDetached = detached;
  detached = false;
  if (createInFlight && !wasDetached && requestedCreateModel === null && activeCreateModel === nextModel) {
    return;
  }
  requestedCreateModel = nextModel;
  if (createInFlight) {
    return;
  }
  createInFlight = true;
  void runCreateQueue();
}

async function runCreateQueue() {
  try {
    while (requestedCreateModel !== null) {
      const model = requestedCreateModel;
      requestedCreateModel = null;
      await createEmulator(model);
    }
  } finally {
    createInFlight = false;
    if (requestedCreateModel !== null) {
      requestCreateEmulator(requestedCreateModel);
    }
  }
}

window.TCodeViewer = {
  setModel(model) {
    const nextModel = normalizeModel(model);
    if (nextModel === currentModel && emulator) {
      setModelLabel(nextModel);
      return;
    }
    requestCreateEmulator(nextModel);
  },
  writeTCode(line) {
    const normalized = String(line || "");
    if (!emulator || typeof emulator.write !== "function") {
      pendingWrites.push(normalized);
      requestCreateEmulator(desiredCreateModel());
      return;
    }
    try {
      emulator.write(normalized);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setState(`write error: ${msg}`);
    }
  },
  resetViewer() {
    requestCreateEmulator(currentModel);
  },
  detachViewer() {
    createSequence += 1;
    detached = true;
    requestedCreateModel = null;
    destroyEmulator();
    pendingWrites.length = 0;
    setState("detached");
  },
};

setModelLabel(currentModel);
setState("loading");
requestCreateEmulator(currentModel);
