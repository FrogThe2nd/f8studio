import { memo, useMemo } from "react";
import { Handle, Position } from "@xyflow/react";
import type { GraphNode, EdgeKind } from "../types";

type Port = { kind: EdgeKind; dir: "in" | "out"; name: string };

function handleId(p: Port): string {
  return `${p.kind}:${p.dir}:${p.name}`;
}

function asStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => String(x ?? "")).filter((s) => s.trim().length > 0);
}

function asObjArray(v: unknown): Record<string, unknown>[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x && typeof x === "object") as Record<string, unknown>[];
}

function stateShowOverride(uiOverrides: Record<string, unknown>, name: string): boolean | null {
  const stateFields = uiOverrides["stateFields"];
  if (!stateFields || typeof stateFields !== "object") return null;
  const rec = stateFields as Record<string, unknown>;
  const ov = rec[name];
  if (!ov || typeof ov !== "object") return null;
  const show = (ov as any)["showOnNode"];
  if (typeof show !== "boolean") return null;
  return show;
}

function extractPorts(node: GraphNode): Port[] {
  const spec = node.spec ?? {};
  const ui = node.uiOverrides ?? {};

  const ports: Port[] = [];

  const isOperator = Object.prototype.hasOwnProperty.call(spec, "operatorClass");
  if (isOperator) {
    for (const p of asStringArray((spec as any)["execInPorts"])) {
      ports.push({ kind: "exec", dir: "in", name: p });
    }
    for (const p of asStringArray((spec as any)["execOutPorts"])) {
      ports.push({ kind: "exec", dir: "out", name: p });
    }
  }

  for (const p of asObjArray((spec as any)["dataInPorts"])) {
    const name = String(p["name"] ?? "").trim();
    if (name) ports.push({ kind: "data", dir: "in", name });
  }
  for (const p of asObjArray((spec as any)["dataOutPorts"])) {
    const name = String(p["name"] ?? "").trim();
    if (name) ports.push({ kind: "data", dir: "out", name });
  }

  for (const f of asObjArray((spec as any)["stateFields"])) {
    const name = String(f["name"] ?? "").trim();
    if (!name) continue;
    const showSpec = Boolean(f["showOnNode"] ?? false);
    const showOv = stateShowOverride(ui as any, name);
    const show = showOv === null ? showSpec : showOv;
    if (!show) continue;

    const access = String(f["access"] ?? "").trim();
    if (access === "rw" || access === "wo") ports.push({ kind: "state", dir: "in", name });
    if (access === "rw" || access === "ro") ports.push({ kind: "state", dir: "out", name });
  }

  return ports;
}

export const StudioNode = memo(function StudioNode(props: { data: { node: GraphNode } }) {
  const node = props.data.node;
  const ports = useMemo(() => extractPorts(node), [node]);

  const label = String((node.spec as any)?.label ?? node.nodeType ?? node.id);
  const isService = !Object.prototype.hasOwnProperty.call(node.spec ?? {}, "operatorClass");
  const svcBadge = isService ? "SERVICE" : "";

  return (
    <div style={{ minWidth: 220, padding: 10, borderRadius: 10, border: "1px solid #2a2a2a", background: "#111" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>{label}</div>
        {svcBadge ? (
          <div className="mono" style={{ fontSize: 10, opacity: 0.7, border: "1px solid rgba(255,255,255,0.12)", padding: "2px 6px", borderRadius: 999 }}>
            {svcBadge}
          </div>
        ) : null}
      </div>
      <div style={{ fontSize: 11, opacity: 0.7, marginBottom: 10 }}>{node.id}</div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <div>
          <div style={{ fontSize: 10, opacity: 0.7, marginBottom: 4 }}>Inputs</div>
          {ports
            .filter((p) => p.dir === "in")
            .map((p) => (
              <div key={handleId(p)} style={{ position: "relative", paddingLeft: 10, marginBottom: 4 }}>
                <Handle
                  id={handleId(p)}
                  type="target"
                  position={Position.Left}
                  style={{ left: -8, background: p.kind === "exec" ? "#f59e0b" : p.kind === "state" ? "#22c55e" : "#60a5fa" }}
                />
                <span style={{ fontSize: 11 }}>
                  {p.kind}:{p.name}
                </span>
              </div>
            ))}
        </div>
        <div>
          <div style={{ fontSize: 10, opacity: 0.7, marginBottom: 4, textAlign: "right" }}>Outputs</div>
          {ports
            .filter((p) => p.dir === "out")
            .map((p) => (
              <div key={handleId(p)} style={{ position: "relative", paddingRight: 10, marginBottom: 4, textAlign: "right" }}>
                <Handle
                  id={handleId(p)}
                  type="source"
                  position={Position.Right}
                  style={{ right: -8, background: p.kind === "exec" ? "#f59e0b" : p.kind === "state" ? "#22c55e" : "#60a5fa" }}
                />
                <span style={{ fontSize: 11 }}>
                  {p.kind}:{p.name}
                </span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
});
