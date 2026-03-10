import { useEffect, useMemo, useRef, useState } from "react";
import { baseWsUrl } from "../api";
import { EventsWsClient } from "../studio/events_ws";
import { VizWsClient } from "../studio/viz_ws";
import { AudioViewer } from "./viz/AudioViewer";
import { TrackViewer } from "./viz/TrackViewer";
import { VideoViewer } from "./viz/VideoViewer";

type Kind = "video" | "audio" | "track";

function qs(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

export function VizDetached() {
  const nodeId = String(qs().get("nodeId") ?? "").trim();
  const kind = (String(qs().get("kind") ?? "video").trim() as Kind) || "video";

  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [jpeg, setJpeg] = useState<Uint8Array | null>(null);
  const [audio, setAudio] = useState<number[] | null>(null);
  const [track, setTrack] = useState<any>(null);
  const [meta, setMeta] = useState<any>(null);

  const eventsUrl = useMemo(() => `${baseWsUrl()}/ws/v1/events`, []);
  const vizUrl = useMemo(() => `${baseWsUrl()}/ws/v1/viz`, []);

  const vizRef = useRef<VizWsClient | null>(null);
  const subId = `${kind}:${nodeId}`;

  useEffect(() => {
    const ev = new EventsWsClient({
      url: eventsUrl,
      onState: () => {},
      onMsg: (m: any) => {
        if (m?.type !== "ui_command") return;
        const payload = m.payload;
        if (!payload || typeof payload !== "object") return;
        if (String(payload.node_id ?? "") !== nodeId) return;
        const cmd = String(payload.command ?? "");
        const p = (payload.payload ?? {}) as Record<string, unknown>;

        if (cmd === "viz.video.set" && (kind === "video" || kind === "track")) setCfg({ command: cmd, ...p });
        if (cmd === "viz.audio.set" && kind === "audio") setCfg({ command: cmd, ...p });
        if (cmd === "viz.track.set" && kind === "track") {
          setCfg({ command: cmd, ...p });
          setTrack(p);
        }
      },
    });
    ev.connect();
    return () => ev.close();
  }, [eventsUrl, nodeId, kind]);

  useEffect(() => {
    if (!nodeId) return;
    const viz = new VizWsClient({
      url: vizUrl,
      onState: () => {},
      onFrame: (f) => {
        setMeta(f.meta);
        if (f.meta.kind === "video") setJpeg(f.payload);
        if (f.meta.kind === "audio") {
          try {
            const txt = new TextDecoder("utf-8").decode(f.payload);
            const arr = JSON.parse(txt);
            if (Array.isArray(arr)) setAudio(arr.map((x) => Number(x)));
          } catch {
            return;
          }
        }
      },
    });
    vizRef.current = viz;
    viz.connect();
    return () => {
      viz.unsub(subId);
      viz.close();
      vizRef.current = null;
    };
  }, [vizUrl, nodeId, subId]);

  useEffect(() => {
    const viz = vizRef.current;
    if (!viz || !cfg) return;
    const cmd = String((cfg as any).command ?? "");
    if (cmd === "viz.video.set") {
      const shmName = String((cfg as any).shmName ?? "").trim();
      if (shmName) viz.sub({ subId, kind: "video", shmName, throttleMs: Number((cfg as any).throttleMs ?? 33) });
    } else if (cmd === "viz.audio.set") {
      const shmName = String((cfg as any).shmName ?? "").trim();
      if (shmName) {
        viz.sub({
          subId,
          kind: "audio",
          shmName,
          throttleMs: Number((cfg as any).throttleMs ?? 20),
          historyMs: Number((cfg as any).historyMs ?? 250),
          channel: Number((cfg as any).channel ?? 0),
        });
      }
    } else if (cmd === "viz.track.set") {
      const shmName = String((cfg as any).videoShmName ?? "").trim();
      if (shmName) viz.sub({ subId, kind: "video", shmName, throttleMs: Number((cfg as any).throttleMs ?? 33) });
    }
  }, [cfg, subId]);

  return (
    <div style={{ padding: 12 }}>
      <div style={{ fontWeight: 900, marginBottom: 8 }}>Viz Detached</div>
      <div className="mono" style={{ opacity: 0.85, marginBottom: 10 }}>
        nodeId={nodeId || "(missing)"} kind={kind}
      </div>
      {!nodeId ? <div className="muted">Missing nodeId in query string.</div> : null}
      {kind === "video" ? <VideoViewer jpegBytes={jpeg} width={Number(meta?.width ?? 0) || undefined} height={Number(meta?.height ?? 0) || undefined} /> : null}
      {kind === "audio" ? <AudioViewer samples={audio} /> : null}
      {kind === "track" ? <TrackViewer jpegBytes={jpeg} videoW={Number(meta?.width ?? 0) || undefined} videoH={Number(meta?.height ?? 0) || undefined} track={track} /> : null}
      {cfg ? (
        <pre className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap", marginTop: 10 }}>
          {JSON.stringify(cfg, null, 2)}
        </pre>
      ) : (
        <div className="muted" style={{ marginTop: 10 }}>
          Waiting for ui_command config...
        </div>
      )}
    </div>
  );
}

