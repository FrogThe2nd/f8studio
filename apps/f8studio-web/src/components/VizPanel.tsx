import { useMemo } from "react";

export type VizKind = "video" | "audio" | "track";

export type VizConfig =
  | { kind: "video"; nodeId: string; shmName: string; throttleMs?: number; width?: number; height?: number }
  | { kind: "audio"; nodeId: string; shmName: string; throttleMs?: number; historyMs?: number; channel?: number }
  | { kind: "track"; nodeId: string; videoShmName: string; flowShmName?: string; throttleMs?: number };

export function VizPanel(props: {
  configs: VizConfig[];
  selected: { nodeId: string; kind: VizKind } | null;
  onSelect: (v: { nodeId: string; kind: VizKind } | null) => void;
  onDetach: (v: { nodeId: string; kind: VizKind }) => void;
}) {
  const sorted = useMemo(() => {
    const xs = [...props.configs];
    xs.sort((a, b) => `${a.kind}:${a.nodeId}`.localeCompare(`${b.kind}:${b.nodeId}`));
    return xs;
  }, [props.configs]);

  return (
    <div>
      <div style={{ fontWeight: 800, marginBottom: 8 }}>Viz</div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
        configs={sorted.length}
      </div>
      <div className="paletteList">
        {sorted.map((c) => {
          const key = `${c.kind}:${c.nodeId}`;
          const active = props.selected && props.selected.nodeId === c.nodeId && props.selected.kind === c.kind;
          const subtitle =
            c.kind === "video" ? c.shmName : c.kind === "audio" ? c.shmName : `video=${c.videoShmName || "(default)"}`;
          return (
            <div key={key} className="paletteItem" style={{ background: active ? "rgba(255,255,255,0.1)" : undefined }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <div>
                  <div style={{ fontWeight: 800, fontSize: 12 }}>
                    {c.kind} <span className="mono" style={{ opacity: 0.8 }}>{c.nodeId}</span>
                  </div>
                  <div className="mono" style={{ fontSize: 11, opacity: 0.7 }}>
                    {subtitle}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button className="btn" onClick={() => props.onSelect({ nodeId: c.nodeId, kind: c.kind })}>
                    Open
                  </button>
                  <button className="btn" onClick={() => props.onDetach({ nodeId: c.nodeId, kind: c.kind })}>
                    Detach
                  </button>
                </div>
              </div>
            </div>
          );
        })}
        {!sorted.length ? <div className="muted">No viz configs yet. Start runtime and deploy a graph with viz operators.</div> : null}
      </div>
      {props.selected ? (
        <div style={{ marginTop: 10 }}>
          <button className="btn" onClick={() => props.onSelect(null)}>
            Close Viewer
          </button>
        </div>
      ) : null}
    </div>
  );
}

