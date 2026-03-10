export type EdgeKind = "exec" | "data" | "state";

export interface GraphNodeUi {
  pos: [number, number];
  size?: [number, number] | null;
  collapsed: boolean;
}

export interface GraphEdgeEndpoint {
  nodeId: string;
  port: string;
}

export interface GraphEdge {
  id: string;
  kind: EdgeKind;
  from: GraphEdgeEndpoint;
  to: GraphEdgeEndpoint;
}

export interface GraphNode {
  id: string;
  nodeType: string;
  spec: Record<string, unknown>;
  state: Record<string, unknown>;
  ui: GraphNodeUi;
  sys: Record<string, unknown>;
  uiOverrides: Record<string, unknown>;
  custom: Record<string, unknown>;
  compat: Record<string, unknown>;
}

export interface GraphDoc {
  schemaVersion: "f8studio-graph/1";
  graphId: string;
  revision: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  settings?: Record<string, unknown>;
  compat?: Record<string, unknown>;
}

export interface ApiOk<T> {
  ok: true;
  [k: string]: unknown;
  payload?: T;
}

export type VariantKind = "operator" | "service";

export interface NodeVariantRecord {
  variantId: string;
  kind: VariantKind;
  baseNodeType: string;
  serviceClass: string;
  operatorClass?: string | null;
  name: string;
  description: string;
  tags: string[];
  spec: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface VariantLibrary {
  schemaVersion: "f8variantlib/1";
  variants: NodeVariantRecord[];
}

export interface NodeTypesResponse {
  ok: true;
  services: Record<string, unknown>[];
  operators: Record<string, unknown>[];
}

export interface RuntimeStatus {
  running: boolean;
  blocked: boolean;
  studioServiceId: string;
  natsUrl: string;
}

export type EventsWsMessage =
  | { type: "runtime.status"; payload: RuntimeStatus }
  | { type: "log"; payload: { level: string; context: string; excType?: string; message: string } }
  | { type: "monitor.update"; payload: Record<string, unknown> }
  | { type: "state.update"; payload: { serviceId: string; nodeId: string; field: string; value: unknown; tsMs: number } }
  | { type: "ui_command"; payload: { node_id: string; command: string; payload: Record<string, unknown>; ts_ms?: number | null } }
  | { type: "pong"; payload: { tsMs: number } };
