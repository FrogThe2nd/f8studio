import { useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, Controls, type Connection, type Node } from "@xyflow/react";
import type { EdgeKind, EventsWsMessage, GraphDoc, GraphEdge, GraphNode, NodeTypesResponse, NodeVariantRecord, RuntimeStatus } from "./types";
import { baseWsUrl, exportToNodegraphqt, getNodeTypes, getVariants, loadLastSession, normalizeGraph, saveLastSession, validateConnection, upsertVariant } from "./api";
import { EventsWsClient } from "./studio/events_ws";
import { VizWsClient } from "./studio/viz_ws";
import { Palette, type PaletteItem } from "./components/Palette";
import { Inspector } from "./components/Inspector";
import { RuntimePanel, type LogLine } from "./components/RuntimePanel";
import { VizPanel, type VizConfig, type VizKind } from "./components/VizPanel";
import { AudioViewer } from "./components/viz/AudioViewer";
import { TrackViewer } from "./components/viz/TrackViewer";
import { VideoViewer } from "./components/viz/VideoViewer";
import { ServiceNode } from "./components/ServiceNode";
import { StudioNode } from "./components/StudioNode";
import { VizDetached } from "./components/VizDetached";
import {
  addEdge,
  addNode,
  computeNodeTypeFromSpec,
  edgeId,
  ensureDocDefaults,
  findContainerServiceAtPoint,
  isContainerServiceSpec,
  getServiceClassFromSpec,
  isContainerService,
  getSvcIdBinding,
  initialStateFromSpec,
  isOperator,
  isService,
  moveServiceAndChildren,
  newNodeId,
  setSvcIdBinding,
  toRfEdges,
  toRfNodes,
  updateNode,
} from "./studio/graph";

type LeftTab = "palette" | "runtime" | "viz";

function blankDoc(): GraphDoc {
  return {
    schemaVersion: "f8studio-graph/1",
    graphId: "studio",
    revision: "1",
    nodes: [],
    edges: [],
    settings: {},
    compat: {},
  };
}

function getMode(): string {
  const p = new URLSearchParams(window.location.search);
  return String(p.get("mode") ?? "").trim();
}

function screenToFlow(rf: any, ev: { clientX: number; clientY: number }) {
  if (rf && typeof rf.screenToFlowPosition === "function") {
    return rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
  }
  if (rf && typeof rf.project === "function") {
    return rf.project({ x: ev.clientX, y: ev.clientY });
  }
  return { x: ev.clientX, y: ev.clientY };
}

function parseHandle(h: string | null | undefined): { kind: EdgeKind; dir: "in" | "out"; port: string } | null {
  const raw = String(h ?? "");
  const parts = raw.split(":");
  if (parts.length !== 3) return null;
  const kind = parts[0] as EdgeKind;
  const dir = parts[1] as "in" | "out";
  const port = parts[2] ?? "";
  if (kind !== "exec" && kind !== "data" && kind !== "state") return null;
  if (dir !== "in" && dir !== "out") return null;
  if (!port.trim()) return null;
  return { kind, dir, port };
}

export function App() {
  if (getMode() === "viz") return <VizDetached />;

  const [doc, setDoc] = useState<GraphDoc>(blankDoc());
  const [openedSchemaVersion, setOpenedSchemaVersion] = useState<string>("f8studio-graph/1");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [leftTab, setLeftTab] = useState<LeftTab>("palette");

  const [nodeTypes, setNodeTypes] = useState<NodeTypesResponse | null>(null);
  const [variants, setVariants] = useState<NodeVariantRecord[]>([]);
  const [busy, setBusy] = useState<string>("");

  // Runtime/events
  const [eventsConnected, setEventsConnected] = useState<boolean>(false);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [monitor, setMonitor] = useState<Record<string, unknown> | null>(null);
  const stateMapRef = useRef<Map<string, { value: unknown; tsMs: number }>>(new Map());
  const [stateCount, setStateCount] = useState<number>(0);

  // Viz
  const [vizConfigs, setVizConfigs] = useState<Map<string, VizConfig>>(new Map());
  const [vizSelected, setVizSelected] = useState<{ nodeId: string; kind: VizKind } | null>(null);
  const [vizConnected, setVizConnected] = useState<boolean>(false);
  const [videoFrame, setVideoFrame] = useState<{ bytes: Uint8Array | null; meta: any } | null>(null);
  const [audioFrame, setAudioFrame] = useState<{ samples: number[] | null; meta: any } | null>(null);
  const [trackPayloadByNode, setTrackPayloadByNode] = useState<Map<string, any>>(new Map());

  const rfRef = useRef<any>(null);
  const lastMouseFlowPosRef = useRef<{ x: number; y: number } | null>(null);
  const paletteSearchRef = useRef<HTMLInputElement | null>(null);

  const eventsRef = useRef<EventsWsClient | null>(null);
  const vizRef = useRef<VizWsClient | null>(null);

  const nodeTypesMap = useMemo(() => ({ studioNode: StudioNode, serviceNode: ServiceNode }), []);

  const rfNodes = useMemo(
    () =>
      toRfNodes(ensureDocDefaults(doc), {
        onServiceResizeEnd: (serviceId, size) => {
          const w = Math.round(Number(size.width));
          const h = Math.round(Number(size.height));
          setDoc((prev) => updateNode(prev, serviceId, (n) => ({ ...n, ui: { ...n.ui, size: [w, h] } })));
        },
      }),
    [doc],
  );
  const rfEdges = useMemo(() => toRfEdges(ensureDocDefaults(doc)), [doc]);

  const selectedNode = useMemo(() => (selectedId ? doc.nodes.find((n) => n.id === selectedId) ?? null : null), [doc, selectedId]);

  const serviceIds = useMemo(() => doc.nodes.filter(isService).map((n) => n.id), [doc]);
  const containerServiceIds = useMemo(() => doc.nodes.filter(isContainerService).map((n) => n.id), [doc]);
  const defaultServiceId = useMemo(() => {
    if (selectedNode && isService(selectedNode)) return selectedNode.id;
    if (selectedNode && isOperator(selectedNode)) {
      const sid = getSvcIdBinding(selectedNode);
      if (sid) return sid;
    }
    return serviceIds[0] ?? "";
  }, [selectedNode, serviceIds]);

  // Load catalog + variants once.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [nt, lib] = await Promise.all([getNodeTypes(), getVariants()]);
        if (!alive) return;
        setNodeTypes(nt);
        setVariants(Array.isArray(lib.variants) ? lib.variants : []);
      } catch (e: any) {
        console.error(e);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Connect events WS.
  useEffect(() => {
    const url = `${baseWsUrl()}/ws/v1/events`;
    const ev = new EventsWsClient({
      url,
      onState: (s) => setEventsConnected(Boolean(s.connected)),
      onMsg: (m: EventsWsMessage) => {
        if (m.type === "runtime.status") setRuntime(m.payload);
        else if (m.type === "log") {
          setLogs((xs) => [...xs, { tsMs: Date.now(), level: String(m.payload.level), context: String(m.payload.context), message: String(m.payload.message), excType: m.payload.excType }]);
        } else if (m.type === "monitor.update") setMonitor(m.payload);
        else if (m.type === "state.update") {
          const k = `${m.payload.serviceId}:${m.payload.nodeId}:${m.payload.field}`;
          stateMapRef.current.set(k, { value: m.payload.value, tsMs: Number(m.payload.tsMs ?? Date.now()) });
          setStateCount(stateMapRef.current.size);
        } else if (m.type === "ui_command") {
          const cmd = String(m.payload.command ?? "");
          const nodeId = String(m.payload.node_id ?? "");
          const payload = (m.payload.payload ?? {}) as Record<string, unknown>;
          if (cmd === "viz.video.set") {
            const shmName = String(payload.shmName ?? "").trim();
            if (shmName) setVizConfigs((prev) => new Map(prev).set(`video:${nodeId}`, { kind: "video", nodeId, shmName, throttleMs: Number(payload.throttleMs ?? 33) }));
          } else if (cmd === "viz.audio.set") {
            const shmName = String(payload.shmName ?? "").trim();
            if (shmName) setVizConfigs((prev) => new Map(prev).set(`audio:${nodeId}`, { kind: "audio", nodeId, shmName, throttleMs: Number(payload.throttleMs ?? 20), historyMs: Number(payload.historyMs ?? 250), channel: Number(payload.channel ?? 0) }));
          } else if (cmd === "viz.track.set") {
            const videoShmName = String(payload.videoShmName ?? "").trim();
            setVizConfigs((prev) => new Map(prev).set(`track:${nodeId}`, { kind: "track", nodeId, videoShmName, throttleMs: Number(payload.throttleMs ?? 33) }));
            setTrackPayloadByNode((prev) => new Map(prev).set(nodeId, payload));
          }
        }
      },
    });
    eventsRef.current = ev;
    ev.connect();
    return () => {
      ev.close();
      eventsRef.current = null;
    };
  }, []);

  // Connect viz WS (lazy but keep it simple: connect once).
  useEffect(() => {
    const url = `${baseWsUrl()}/ws/v1/viz`;
    const viz = new VizWsClient({
      url,
      onState: (s) => setVizConnected(Boolean(s.connected)),
      onFrame: (f) => {
        const kind = String(f.meta.kind ?? "");
        if (kind === "video") setVideoFrame({ bytes: f.payload, meta: f.meta });
        if (kind === "audio") {
          try {
            const txt = new TextDecoder("utf-8").decode(f.payload);
            const arr = JSON.parse(txt);
            if (Array.isArray(arr)) setAudioFrame({ samples: arr.map((x) => Number(x)), meta: f.meta });
          } catch {
            return;
          }
        }
      },
    });
    vizRef.current = viz;
    viz.connect();
    return () => {
      viz.close();
      vizRef.current = null;
    };
  }, []);

  const loadLast = async () => {
    setBusy("Loading last session...");
    try {
      const last = await loadLastSession();
      if (!last.exists || !last.payload) {
        setDoc(blankDoc());
        setOpenedSchemaVersion("f8studio-graph/1");
        setWarnings([]);
        return;
      }
      setOpenedSchemaVersion(String(last.payload.schemaVersion ?? "unknown"));
      const out = await normalizeGraph(last.payload);
      setDoc(out.doc);
      setWarnings(out.warnings);
    } finally {
      setBusy("");
    }
  };

  const saveLast = async () => {
    setBusy("Saving...");
    try {
      if (openedSchemaVersion === "f8studio-session/1") {
        const envelope = await exportToNodegraphqt(doc);
        await saveLastSession(envelope);
      } else {
        await saveLastSession(doc);
      }
    } finally {
      setBusy("");
    }
  };

  const onConnect = async (c: Connection) => {
    const srcId = String(c.source ?? "");
    const dstId = String(c.target ?? "");
    const sh = parseHandle(c.sourceHandle);
    const th = parseHandle(c.targetHandle);
    if (!srcId || !dstId || !sh || !th) return;
    if (sh.dir !== "out" || th.dir !== "in") return;
    if (sh.kind !== th.kind) return;

    setBusy("Validating connection...");
    try {
      const res = await validateConnection({ doc, kind: sh.kind, from: { nodeId: srcId, port: sh.port }, to: { nodeId: dstId, port: th.port } });
      if (!res.allowed) {
        alert(res.reason || "Connection rejected");
        return;
      }
      const e: GraphEdge = { id: "tmp", kind: sh.kind, from: { nodeId: srcId, port: sh.port }, to: { nodeId: dstId, port: th.port } };
      e.id = edgeId(doc, e);
      setDoc((prev) => addEdge(prev, e));
    } finally {
      setBusy("");
    }
  };

  const onNodeDragStop = (_: any, n: Node) => {
    const nodeId = String(n.id);
    const abs = (n as any).positionAbsolute ? (n as any).positionAbsolute : n.position;
    const x = Number(abs?.x ?? 0);
    const y = Number(abs?.y ?? 0);
    const cur = doc.nodes.find((nn) => nn.id === nodeId);
    if (!cur) return;

    if (isService(cur)) {
      const oldX = Number(cur.ui.pos?.[0] ?? 0);
      const oldY = Number(cur.ui.pos?.[1] ?? 0);
      const dx = x - oldX;
      const dy = y - oldY;
      setDoc((prev) => moveServiceAndChildren(prev, nodeId, { dx, dy }));
      return;
    }

    // Operator absolute position update.
    let nextDoc = updateNode(doc, nodeId, (nn) => ({ ...nn, ui: { ...nn.ui, pos: [x, y] } }));

    // Container rebind based on where it ended up (container services only, matching serviceClass).
    const opSvcClass = getServiceClassFromSpec(cur.spec ?? {});
    const curBoundId = getSvcIdBinding(cur);
    const curBound = nextDoc.nodes.find((nn) => nn.id === curBoundId) ?? null;
    const curBoundOk = Boolean(curBound && isContainerService(curBound) && getServiceClassFromSpec(curBound.spec ?? {}) === opSvcClass);

    // If already bound to a valid container, keep it stable (don't switch due to overlaps).
    const svc = curBoundOk ? curBound : findContainerServiceAtPoint(nextDoc, { x, y }, opSvcClass);
    if (svc) nextDoc = updateNode(nextDoc, nodeId, (nn) => setSvcIdBinding(nn, svc.id));
    else if (!curBoundOk) nextDoc = updateNode(nextDoc, nodeId, (nn) => setSvcIdBinding(nn, ""));

    // If we are bound and ReactFlow expanded the parent (expandParent), persist the parent's new size/pos once.
    // This avoids the old "dimensions feedback loop" while still supporting container auto-grow.
    const rf = rfRef.current;
    const boundId = svc ? svc.id : curBoundId;
    if (rf && boundId) {
      try {
        const rfNodesNow = rf.getNodes ? (rf.getNodes() as any[]) : [];
        const parentNow = rfNodesNow.find((nn) => String(nn.id) === String(boundId));
        const w = Math.round(Number(parentNow?.width ?? parentNow?.measured?.width ?? 0));
        const h = Math.round(Number(parentNow?.height ?? parentNow?.measured?.height ?? 0));
        const px = Number(parentNow?.position?.x ?? 0);
        const py = Number(parentNow?.position?.y ?? 0);
        if (w > 50 && h > 50) {
          nextDoc = updateNode(nextDoc, boundId, (sn) => ({ ...sn, ui: { ...sn.ui, pos: [px, py], size: [w, h] } }));
        }
      } catch {
        // Non-fatal: if we can't read internal nodes, we still persist the operator move.
      }
    }
    setDoc(nextDoc);
  };

  const svcPickerRef = useRef<{ pending: PaletteItem; pos: { x: number; y: number }; serviceClass: string } | null>(null);
  const [svcPicker, setSvcPicker] = useState<{ pending: PaletteItem; pos: { x: number; y: number }; serviceClass: string } | null>(null);

  const createAt = (item: PaletteItem, pos: { x: number; y: number }) => {
    const existing = new Set(doc.nodes.map((n) => n.id));
    const newId = newNodeId(existing);

    if (item.kind === "service") {
      const nodeType = computeNodeTypeFromSpec(item.spec);
      const isContainer = isContainerServiceSpec(item.spec);
      const node: GraphNode = {
        id: newId,
        nodeType,
        spec: item.spec,
        state: initialStateFromSpec(item.spec),
        ui: { pos: [pos.x, pos.y], size: isContainer ? [900, 600] : null, collapsed: false },
        sys: {},
        uiOverrides: {},
        custom: isContainer ? { svcId: newId } : {},
        compat: {},
      };
      setDoc((prev) => addNode(prev, node));
      return;
    }

    if (item.kind === "variant" && item.record.kind === "service") {
      const isContainer = isContainerServiceSpec(item.record.spec);
      const node: GraphNode = {
        id: newId,
        nodeType: item.record.baseNodeType,
        spec: item.record.spec,
        state: initialStateFromSpec(item.record.spec),
        ui: { pos: [pos.x, pos.y], size: isContainer ? [900, 600] : null, collapsed: false },
        sys: {},
        uiOverrides: {},
        custom: isContainer ? { svcId: newId } : {},
        compat: {},
      };
      setDoc((prev) => addNode(prev, node));
      return;
    }

    const opSpec = item.kind === "operator" ? item.spec : item.record.spec;
    const baseNodeType = item.kind === "variant" ? item.record.baseNodeType : computeNodeTypeFromSpec(opSpec);
    const opSvcClass = getServiceClassFromSpec(opSpec);
    const containers = doc.nodes.filter((n) => isContainerService(n) && getServiceClassFromSpec(n.spec ?? {}) === opSvcClass);
    const hit = containers.length ? findContainerServiceAtPoint(doc, pos, opSvcClass) : null;
    if (!hit && containers.length) {
      svcPickerRef.current = { pending: item, pos, serviceClass: opSvcClass };
      setSvcPicker({ pending: item, pos, serviceClass: opSvcClass });
      return;
    }
    if (!containers.length) {
      alert(`No container service for serviceClass="${opSvcClass}" exists. Create a container service first (rendererClass="default_container").`);
      return;
    }
    const svcId = hit ? hit.id : containers[0].id;

    const node: GraphNode = {
      id: newId,
      nodeType: baseNodeType,
      spec: opSpec,
      state: initialStateFromSpec(opSpec),
      ui: { pos: [pos.x, pos.y], size: null, collapsed: false },
      sys: {},
      uiOverrides: {},
      custom: { svcId },
      compat: {},
    };
    setDoc((prev) => addNode(prev, node));
  };

  const onPalettePick = (item: PaletteItem) => {
    const pos = lastMouseFlowPosRef.current ?? { x: 0, y: 0 };
    createAt(item, pos);
  };

  const onPaletteDragStart = (ev: React.DragEvent, item: PaletteItem) => {
    ev.dataTransfer.setData("application/x-f8palette", JSON.stringify(item));
    ev.dataTransfer.effectAllowed = "copy";
  };

  const onDrop = (ev: React.DragEvent) => {
    ev.preventDefault();
    const raw = ev.dataTransfer.getData("application/x-f8palette");
    if (!raw) return;
    let item: PaletteItem;
    try {
      item = JSON.parse(raw);
    } catch {
      return;
    }
    const rf = rfRef.current;
    const p = screenToFlow(rf, ev);
    createAt(item, { x: Number(p.x), y: Number(p.y) });
  };

  const onDragOver = (ev: React.DragEvent) => {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  };

  const pickService = (svcId: string) => {
    const pending = svcPickerRef.current;
    if (!pending) {
      setSvcPicker(null);
      return;
    }
    setSvcPicker(null);
    svcPickerRef.current = null;
    const item = pending.pending;
    const pos = pending.pos;
    const existing = new Set(doc.nodes.map((n) => n.id));
    const newId = newNodeId(existing);
    if (item.kind === "variant" && item.record.kind === "service") {
      const node: GraphNode = {
        id: newId,
        nodeType: item.record.baseNodeType,
        spec: item.record.spec,
        state: initialStateFromSpec(item.record.spec),
        ui: { pos: [pos.x, pos.y], size: [900, 600], collapsed: false },
        sys: {},
        uiOverrides: {},
        custom: { svcId: newId },
        compat: {},
      };
      setDoc((prev) => addNode(prev, node));
      return;
    }

    // svcPicker is only used for operator creation. Be explicit so TS can narrow safely.
    if (item.kind !== "operator" && item.kind !== "variant") return;

    let opSpec: Record<string, unknown>;
    let baseNodeType: string;
    if (item.kind === "operator") {
      opSpec = item.spec;
      baseNodeType = computeNodeTypeFromSpec(opSpec);
    } else {
      opSpec = item.record.spec;
      baseNodeType = item.record.baseNodeType;
    }
    const node: GraphNode = {
      id: newId,
      nodeType: baseNodeType,
      spec: opSpec,
      state: initialStateFromSpec(opSpec),
      ui: { pos: [pos.x, pos.y], size: null, collapsed: false },
      sys: {},
      uiOverrides: {},
      custom: { svcId },
      compat: {},
    };
    setDoc((prev) => addNode(prev, node));
  };

  const requestStart = () => eventsRef.current?.send({ type: "runtime.start" });
  const requestStop = () => eventsRef.current?.send({ type: "runtime.stop" });

  const vizConfigList = useMemo(() => Array.from(vizConfigs.values()), [vizConfigs]);

  const vizPrevSelRef = useRef<{ nodeId: string; kind: VizKind } | null>(null);

  const openViz = (sel: { nodeId: string; kind: VizKind } | null) => {
    const viz = vizRef.current;
    const prev = vizPrevSelRef.current;
    if (viz && prev) {
      if (prev.kind === "video") viz.unsub(`video:${prev.nodeId}`);
      if (prev.kind === "audio") viz.unsub(`audio:${prev.nodeId}`);
      if (prev.kind === "track") viz.unsub(`trackVideo:${prev.nodeId}`);
    }
    vizPrevSelRef.current = sel;
    setVizSelected(sel);
    setVideoFrame(null);
    setAudioFrame(null);
    if (!viz) return;
    if (!sel) {
      // best-effort unsub all
      return;
    }
    if (sel.kind === "video") {
      const cfg = vizConfigs.get(`video:${sel.nodeId}`) as any;
      if (cfg?.shmName) viz.sub({ subId: `video:${sel.nodeId}`, kind: "video", shmName: cfg.shmName, throttleMs: cfg.throttleMs ?? 33 });
    } else if (sel.kind === "audio") {
      const cfg = vizConfigs.get(`audio:${sel.nodeId}`) as any;
      if (cfg?.shmName) viz.sub({ subId: `audio:${sel.nodeId}`, kind: "audio", shmName: cfg.shmName, throttleMs: cfg.throttleMs ?? 20, historyMs: cfg.historyMs ?? 250, channel: cfg.channel ?? 0 });
    } else if (sel.kind === "track") {
      const cfg = vizConfigs.get(`track:${sel.nodeId}`) as any;
      if (cfg?.videoShmName) viz.sub({ subId: `trackVideo:${sel.nodeId}`, kind: "video", shmName: cfg.videoShmName, throttleMs: cfg.throttleMs ?? 33 });
    }
  };

  const detachViz = (sel: { nodeId: string; kind: VizKind }) => {
    const qs = new URLSearchParams({ mode: "viz", nodeId: sel.nodeId, kind: sel.kind });
    window.open(`/?${qs.toString()}`, "_blank", "noopener,noreferrer");
  };

  const saveVariantFromInspector = async (record: NodeVariantRecord) => {
    const out = await upsertVariant(record);
    setVariants((xs) => {
      const idx = xs.findIndex((x) => x.variantId === out.variantId);
      if (idx >= 0) {
        const next = [...xs];
        next[idx] = out;
        return next;
      }
      return [out, ...xs];
    });
  };

  const applyVariantToNode = (record: NodeVariantRecord) => {
    if (!selectedNode) return;
    setDoc((prev) => {
      const patched = updateNode(prev, selectedNode.id, (n) => ({
        ...n,
        nodeType: record.baseNodeType,
        spec: record.spec,
      }));

      const after = patched.nodes.find((n) => n.id === selectedNode.id) ?? null;
      if (!after || !isOperator(after)) return patched;

      const newSvcClass = getServiceClassFromSpec(record.spec ?? {});
      const boundId = getSvcIdBinding(after);
      const bound = patched.nodes.find((x) => x.id === boundId) ?? null;
      const ok = bound && isContainerService(bound) && getServiceClassFromSpec(bound.spec ?? {}) === newSvcClass;
      if (ok) return patched;

      return updateNode(patched, selectedNode.id, (nn) => setSvcIdBinding(nn, ""));
    });
  };

  const leftContent = () => {
    if (leftTab === "palette") {
      return (
        <Palette
          services={(nodeTypes?.services ?? []) as any}
          operators={(nodeTypes?.operators ?? []) as any}
          variants={variants}
          onPick={onPalettePick}
          onDragStart={onPaletteDragStart}
          searchInputRef={paletteSearchRef}
        />
      );
    }
    if (leftTab === "runtime") {
      return (
        <RuntimePanel
          doc={doc}
          runtime={runtime}
          natsUrl={runtime?.natsUrl ?? "nats://127.0.0.1:4222"}
          serviceIds={serviceIds.length ? serviceIds : ["studio"]}
          defaultServiceId={defaultServiceId || serviceIds[0] || "studio"}
          onRequestStart={requestStart}
          onRequestStop={requestStop}
          logs={logs}
          monitor={monitor}
          stateCount={stateCount}
        />
      );
    }
    return (
      <div>
        <VizPanel configs={vizConfigList} selected={vizSelected} onSelect={openViz} onDetach={detachViz} />
        <div style={{ marginTop: 14 }}>
          <div className="muted mono" style={{ fontSize: 12, marginBottom: 6 }}>
            viz ws: {vizConnected ? "connected" : "disconnected"}
          </div>
          {vizSelected?.kind === "video" ? <VideoViewer jpegBytes={videoFrame?.bytes ?? null} width={Number(videoFrame?.meta?.width ?? 0) || undefined} height={Number(videoFrame?.meta?.height ?? 0) || undefined} /> : null}
          {vizSelected?.kind === "audio" ? <AudioViewer samples={audioFrame?.samples ?? null} /> : null}
          {vizSelected?.kind === "track" ? (
            <TrackViewer
              jpegBytes={videoFrame?.bytes ?? null}
              videoW={Number(videoFrame?.meta?.width ?? 0) || undefined}
              videoH={Number(videoFrame?.meta?.height ?? 0) || undefined}
              track={trackPayloadByNode.get(vizSelected.nodeId) ?? null}
            />
          ) : null}
        </div>
      </div>
    );
  };

  const inspectorVizSummary = useMemo(() => {
    if (!selectedNode) return null;
    const v = vizConfigs.get(`video:${selectedNode.id}`);
    const a = vizConfigs.get(`audio:${selectedNode.id}`);
    const t = vizConfigs.get(`track:${selectedNode.id}`);
    const out: any = {};
    if (v) out.video = v;
    if (a) out.audio = a;
    if (t) out.track = t;
    return Object.keys(out).length ? out : null;
  }, [selectedNode, vizConfigs]);

  useEffect(() => {
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.key !== "Tab") return;
      const target = ev.target as any;
      const tag = String(target?.tagName ?? "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (leftTab !== "palette") setLeftTab("palette");
      const el = paletteSearchRef.current;
      if (el) {
        ev.preventDefault();
        el.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [leftTab]);

  return (
    <div className="app">
      <div className="panel">
        <div className="toolbar">
          <button className="btn" onClick={loadLast}>
            Load last
          </button>
          <button className="btn" onClick={saveLast}>
            Save last
          </button>
          <div className="muted">{busy ? busy : `Opened as: ${openedSchemaVersion}`}</div>
        </div>
        <div className="tabs">
          <button className={`tab ${leftTab === "palette" ? "active" : ""}`} onClick={() => setLeftTab("palette")}>
            Palette
          </button>
          <button className={`tab ${leftTab === "runtime" ? "active" : ""}`} onClick={() => setLeftTab("runtime")}>
            Runtime
          </button>
          <button className={`tab ${leftTab === "viz" ? "active" : ""}`} onClick={() => setLeftTab("viz")}>
            Viz
          </button>
        </div>
        <div className="muted mono" style={{ fontSize: 11, marginBottom: 10 }}>
          events ws: {eventsConnected ? "connected" : "disconnected"}
        </div>
        {warnings.length ? (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontWeight: 800, marginBottom: 6 }}>Warnings</div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {warnings.slice(0, 8).map((w, i) => (
                <li key={i} className="mono">
                  {w}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {leftContent()}
      </div>

      <div style={{ height: "100%" }} onDrop={onDrop} onDragOver={onDragOver}>
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={nodeTypesMap}
          onInit={(rf) => (rfRef.current = rf)}
          onConnect={onConnect}
          onNodeDragStop={onNodeDragStop}
          onNodeClick={(_, n) => setSelectedId(String(n.id))}
          onPaneMouseMove={(ev) => {
            const rf = rfRef.current;
            if (!rf) return;
            const p = screenToFlow(rf, ev as any);
            lastMouseFlowPosRef.current = { x: Number(p.x), y: Number(p.y) };
          }}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      <div className="panel right">
        {selectedNode ? (
          <Inspector
            node={selectedNode}
            variants={variants}
            vizSummary={inspectorVizSummary}
            onPatchNode={(patch) => setDoc((prev) => updateNode(prev, selectedNode.id, patch))}
            onSaveVariant={saveVariantFromInspector}
            onApplyVariant={applyVariantToNode}
          />
        ) : (
          <div className="muted">Select a node.</div>
        )}
      </div>

      {svcPicker ? (
        <div
          style={{
            position: "fixed",
            left: 0,
            top: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
            zIndex: 9999,
          }}
          onClick={() => setSvcPicker(null)}
        >
          <div
            style={{ width: 520, maxWidth: "100%", background: "rgba(10,12,18,0.95)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 14, padding: 14 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontWeight: 900, marginBottom: 10 }}>Select Service Container</div>
            <div className="fieldRow">
              <label>serviceId</label>
              <select onChange={(e) => pickService(e.target.value)} defaultValue="">
                <option value="" disabled>
                  (select)
                </option>
                {doc.nodes
                  .filter((n) => isContainerService(n) && getServiceClassFromSpec(n.spec ?? {}) === svcPicker.serviceClass)
                  .map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.id} ({getServiceClassFromSpec(n.spec ?? {})})
                    </option>
                  ))}
              </select>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              No compatible container was found at drop position. Pick one to bind `custom.svcId` (must match serviceClass="{svcPicker.serviceClass}").
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
