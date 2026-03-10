import type { EdgeKind, GraphDoc, NodeTypesResponse, RuntimeStatus, VariantLibrary, NodeVariantRecord } from "./types";

const DEFAULT_BASE = "http://127.0.0.1:8765";

function baseUrl(): string {
  return (import.meta as any).env?.VITE_BACKEND_HTTP ?? DEFAULT_BASE;
}

const DEFAULT_WS = "ws://127.0.0.1:8766";

export function baseWsUrl(): string {
  return (import.meta as any).env?.VITE_BACKEND_WS ?? DEFAULT_WS;
}

async function readJson(res: Response): Promise<any> {
  const text = await res.text();
  if (!text) return null;
  return JSON.parse(text);
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, { method: "GET" });
  const data = await readJson(res);
  if (!res.ok || !data?.ok) throw new Error(data?.error?.message ?? res.statusText);
  return data as T;
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await readJson(res);
  if (!res.ok || !data?.ok) throw new Error(data?.error?.message ?? res.statusText);
  return data as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await readJson(res);
  if (!res.ok || !data?.ok) throw new Error(data?.error?.message ?? res.statusText);
  return data as T;
}

export async function loadLastSession(): Promise<{ exists: boolean; payload?: any }> {
  const res = await apiGet<any>("/api/v1/session/last");
  return { exists: Boolean(res.exists), payload: res.payload };
}

export async function saveLastSession(payload: any): Promise<void> {
  await apiPut<any>("/api/v1/session/last", payload);
}

export async function normalizeGraph(payload: any): Promise<{ doc: GraphDoc; warnings: string[] }> {
  const res = await apiPost<any>("/api/v1/graph/normalize", payload);
  return { doc: res.doc as GraphDoc, warnings: (res.warnings ?? []) as string[] };
}

export async function exportToNodegraphqt(doc: GraphDoc): Promise<any> {
  const res = await apiPost<any>("/api/v1/session/export/nodegraphqt", doc);
  return res.envelope;
}

export async function getNodeTypes(): Promise<NodeTypesResponse> {
  return apiGet<NodeTypesResponse>("/api/v1/node-types");
}

export async function getVariants(): Promise<VariantLibrary> {
  const res = await apiGet<any>("/api/v1/variants");
  return res.library as VariantLibrary;
}

export async function upsertVariant(record: NodeVariantRecord): Promise<NodeVariantRecord> {
  const res = await apiPost<any>("/api/v1/variants", record);
  return res.record as NodeVariantRecord;
}

export async function deleteVariant(variantId: string): Promise<boolean> {
  const res = await fetch(`${baseUrl()}/api/v1/variants/${encodeURIComponent(variantId)}`, { method: "DELETE" });
  const data = await readJson(res);
  if (!res.ok || !data?.ok) throw new Error(data?.error?.message ?? res.statusText);
  return Boolean(data.deleted);
}

export async function runtimeStatus(): Promise<RuntimeStatus> {
  const res = await apiGet<any>("/api/v1/runtime/status");
  return res.status as RuntimeStatus;
}

export async function validateConnection(args: {
  doc: GraphDoc;
  kind: EdgeKind;
  from: { nodeId: string; port: string };
  to: { nodeId: string; port: string };
}): Promise<{ allowed: boolean; reason: string }> {
  const res = await apiPost<any>("/api/v1/graph/validate-connection", args);
  return { allowed: Boolean(res.allowed), reason: String(res.reason ?? "") };
}

export async function compileGraph(doc: GraphDoc): Promise<{ compiled: any }> {
  const res = await apiPost<any>("/api/v1/graph/compile", doc);
  return { compiled: res.compiled };
}

export async function deployRuntime(args: { serviceId: string; natsUrl?: string; doc: GraphDoc }): Promise<void> {
  await apiPost<any>("/api/v1/runtime/deploy", args);
}
