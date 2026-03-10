import type { GraphDoc, GraphNode, GraphEdge } from "../types";

const BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

export function getServiceClassFromSpec(spec: Record<string, unknown>): string {
  return String((spec as any).serviceClass ?? "").trim();
}

export function getOperatorClassFromSpec(spec: Record<string, unknown>): string {
  return String((spec as any).operatorClass ?? "").trim();
}

export function getRendererClassFromSpec(spec: Record<string, unknown>): string {
  return String((spec as any).rendererClass ?? "").trim();
}

export function isOperator(node: GraphNode): boolean {
  return Object.prototype.hasOwnProperty.call(node.spec ?? {}, "operatorClass");
}

export function isService(node: GraphNode): boolean {
  return !isOperator(node);
}

export function isContainerServiceSpec(spec: Record<string, unknown>): boolean {
  // Locked decision: rendererClass drives container classification (e.g. pyengine: "default_container").
  return getRendererClassFromSpec(spec) === "default_container";
}

export function isContainerService(node: GraphNode): boolean {
  return isService(node) && isContainerServiceSpec(node.spec ?? {});
}

export function isStandaloneService(node: GraphNode): boolean {
  return isService(node) && !isContainerService(node);
}

export function getSvcIdBinding(node: GraphNode): string {
  const c = node.custom ?? {};
  const s = node.state ?? {};
  const a = String((c as any).svcId ?? "").trim();
  if (a) return a;
  const b = String((s as any).svcId ?? "").trim();
  return b;
}

export function setSvcIdBinding(node: GraphNode, svcId: string): GraphNode {
  const nextCustom = { ...(node.custom ?? {}), svcId };
  return { ...node, custom: nextCustom };
}

export function computeNodeTypeFromSpec(spec: Record<string, unknown>): string {
  const serviceClass = getServiceClassFromSpec(spec);
  const operatorClass = getOperatorClassFromSpec(spec);
  if (operatorClass) return `${serviceClass}.${operatorClass}`;
  return `svc.${serviceClass}`;
}

export function initialStateFromSpec(spec: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const sfs = (spec as any).stateFields;
  if (!Array.isArray(sfs)) return out;
  for (const f of sfs) {
    if (!f || typeof f !== "object") continue;
    const name = String((f as any).name ?? "").trim();
    if (!name) continue;
    const access = String((f as any).access ?? "").trim();
    if (access === "ro") continue;
    const schema = (f as any).valueSchema;
    if (schema && typeof schema === "object" && "default" in (schema as any)) {
      const d = (schema as any).default;
      if (d !== null && d !== undefined) out[name] = d;
    }
  }
  return out;
}

export function newNodeId(existing: Set<string>, preferredLen = 4): string {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const len = Math.min(8, preferredLen + Math.floor(attempt / 40));
    let out = "";
    for (let i = 0; i < len; i += 1) {
      const idx = Math.floor(Math.random() * BASE62.length);
      out += BASE62[idx];
    }
    if (!out.includes(".") && !existing.has(out)) return out;
  }
  // Fallback: uuid without dots.
  const u = crypto.randomUUID().replaceAll("-", "");
  return existing.has(u) ? `${u}${Date.now()}` : u;
}

export function ensureDocDefaults(doc: GraphDoc): GraphDoc {
  return {
    ...doc,
    settings: doc.settings ?? {},
    compat: doc.compat ?? {},
    nodes: doc.nodes ?? [],
    edges: doc.edges ?? [],
  };
}

export function findContainerServiceAtPoint(doc: GraphDoc, p: { x: number; y: number }, serviceClass?: string): GraphNode | null {
  const wantSvc = String(serviceClass ?? "").trim();
  const services = doc.nodes.filter((n) => {
    if (!isContainerService(n)) return false;
    if (!wantSvc) return true;
    return getServiceClassFromSpec(n.spec ?? {}) === wantSvc;
  });
  let best: { area: number; node: GraphNode } | null = null;
  for (const svc of services) {
    const [x, y] = svc.ui?.pos ?? [0, 0];
    const size = svc.ui?.size ?? [900, 600];
    const w = Number(size?.[0] ?? 900);
    const h = Number(size?.[1] ?? 600);
    if (p.x >= x && p.x <= x + w && p.y >= y && p.y <= y + h) {
      const area = w * h;
      if (!best || area < best.area) best = { area, node: svc };
    }
  }
  return best ? best.node : null;
}

export function toRfNodes(
  doc: GraphDoc,
  opts?: { onServiceResizeEnd?: (serviceId: string, size: { width: number; height: number }) => void },
): any[] {
  const byId = new Map(doc.nodes.map((n) => [n.id, n]));
  return doc.nodes.map((n) => {
    if (isContainerService(n)) {
      const size = n.ui?.size ?? [900, 600];
      const w = Number(size?.[0] ?? 900);
      const h = Number(size?.[1] ?? 600);
      return {
        id: n.id,
        type: "serviceNode",
        position: { x: n.ui.pos?.[0] ?? 0, y: n.ui.pos?.[1] ?? 0 },
        data: {
          node: n,
          onResizeEnd: opts?.onServiceResizeEnd ? (sz: { width: number; height: number }) => opts.onServiceResizeEnd!(n.id, sz) : undefined,
        },
        style: { zIndex: 0, width: w, height: h, boxSizing: "border-box" },
      };
    }

    if (isStandaloneService(n)) {
      return {
        id: n.id,
        type: "studioNode",
        position: { x: n.ui.pos?.[0] ?? 0, y: n.ui.pos?.[1] ?? 0 },
        data: { node: n },
        style: { zIndex: 5 },
      };
    }

    const svcId = getSvcIdBinding(n);
    const parent = byId.get(svcId);
    const opSvcClass = getServiceClassFromSpec(n.spec ?? {});
    const parentOk = parent && isContainerService(parent) && getServiceClassFromSpec(parent.spec ?? {}) === opSvcClass;

    const parentNode = parentOk ? parent : null;
    const absX = n.ui.pos?.[0] ?? 0;
    const absY = n.ui.pos?.[1] ?? 0;
    const rel = parentNode ? { x: absX - (parentNode.ui.pos?.[0] ?? 0), y: absY - (parentNode.ui.pos?.[1] ?? 0) } : { x: absX, y: absY };
    return {
      id: n.id,
      type: "studioNode",
      position: rel,
      data: { node: n },
      parentId: parentNode ? parentNode.id : undefined,
      // We want children to stay conceptually inside their container; allow the container to grow if needed.
      // (Extent='parent' would prevent growing by constraining the child, so we leave extent unset and rely on expandParent.)
      expandParent: parentNode ? true : undefined,
      style: { zIndex: 10 },
    };
  });
}

export function toRfEdges(doc: GraphDoc): any[] {
  return doc.edges.map((e: GraphEdge) => ({
    id: e.id,
    source: e.from.nodeId,
    target: e.to.nodeId,
    sourceHandle: `${e.kind}:out:${e.from.port}`,
    targetHandle: `${e.kind}:in:${e.to.port}`,
    label: `${e.kind}:${e.from.port} -> ${e.to.port}`,
    type: "smoothstep",
  }));
}

export function edgeId(doc: GraphDoc, e: GraphEdge): string {
  const base = `${e.kind}:${e.from.nodeId}:${e.from.port}->${e.to.nodeId}:${e.to.port}`;
  const existing = new Set(doc.edges.map((x) => x.id));
  if (!existing.has(base)) return base;
  let idx = 2;
  while (existing.has(`${base}#${idx}`)) idx += 1;
  return `${base}#${idx}`;
}

export function updateNode(doc: GraphDoc, nodeId: string, patch: (n: GraphNode) => GraphNode): GraphDoc {
  return { ...doc, nodes: doc.nodes.map((n) => (n.id === nodeId ? patch(n) : n)) };
}

export function addNode(doc: GraphDoc, node: GraphNode): GraphDoc {
  return { ...doc, nodes: [...doc.nodes, node] };
}

export function addEdge(doc: GraphDoc, edge: GraphEdge): GraphDoc {
  return { ...doc, edges: [...doc.edges, edge] };
}

export function moveServiceAndChildren(doc: GraphDoc, serviceId: string, delta: { dx: number; dy: number }): GraphDoc {
  if (!delta.dx && !delta.dy) return doc;
  return {
    ...doc,
    nodes: doc.nodes.map((n) => {
      if (n.id === serviceId) {
        return { ...n, ui: { ...n.ui, pos: [Number(n.ui.pos[0]) + delta.dx, Number(n.ui.pos[1]) + delta.dy] } };
      }
      if (isOperator(n) && getSvcIdBinding(n) === serviceId) {
        return { ...n, ui: { ...n.ui, pos: [Number(n.ui.pos[0]) + delta.dx, Number(n.ui.pos[1]) + delta.dy] } };
      }
      return n;
    }),
  };
}
