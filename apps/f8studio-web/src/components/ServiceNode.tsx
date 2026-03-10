import { memo, useMemo } from "react";
import { NodeResizer } from "@xyflow/react";
import type { GraphNode } from "../types";

export const ServiceNode = memo(function ServiceNode(props: { data: { node: GraphNode; onResizeEnd?: (size: { width: number; height: number }) => void }; selected?: boolean }) {
  const node = props.data.node;
  const label = useMemo(() => String((node.spec as any)?.label ?? node.nodeType ?? node.id), [node]);

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        borderRadius: 16,
        border: "1px solid rgba(255,255,255,0.14)",
        background:
          "linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.02) 70%, rgba(0,0,0,0.08) 100%)",
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.07)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <NodeResizer
        minWidth={420}
        minHeight={260}
        isVisible={Boolean(props.selected)}
        onResizeEnd={(_, params) => {
          const w = Math.round(Number((params as any)?.width ?? 0));
          const h = Math.round(Number((params as any)?.height ?? 0));
          if (w > 10 && h > 10 && props.data.onResizeEnd) props.data.onResizeEnd({ width: w, height: h });
        }}
      />
      <div style={{ padding: 12, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontWeight: 800, letterSpacing: 0.3 }}>{label}</div>
        <div style={{ fontSize: 11, opacity: 0.7 }} className="mono">
          {node.id}
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: 42,
          background: "linear-gradient(180deg, rgba(0,0,0,0) 0%, rgba(0,0,0,0.35) 100%)",
          pointerEvents: "none",
        }}
      />
    </div>
  );
});
