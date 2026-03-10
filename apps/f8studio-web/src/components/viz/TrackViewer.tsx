import { useEffect, useMemo, useRef } from "react";
import { VideoViewer } from "./VideoViewer";

type TrackSetPayload = {
  width?: number;
  height?: number;
  tracks?: { id: number; history: { tsMs: number; bbox?: number[]; keypoints?: any; kind?: string }[] }[];
};

export function TrackViewer(props: { jpegBytes: Uint8Array | null; videoW?: number; videoH?: number; track: TrackSetPayload | null }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const dims = useMemo(() => {
    const w = Number(props.videoW ?? props.track?.width ?? 0);
    const h = Number(props.videoH ?? props.track?.height ?? 0);
    return { w: w > 0 ? w : 640, h: h > 0 ? h : 360 };
  }, [props.videoW, props.videoH, props.track]);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = c.width;
    const h = c.height;
    ctx.clearRect(0, 0, w, h);
    const track = props.track;
    if (!track || !Array.isArray(track.tracks)) return;
    ctx.strokeStyle = "rgba(34,197,94,0.95)";
    ctx.lineWidth = 2;
    for (const t of track.tracks) {
      const hist = Array.isArray(t.history) ? t.history : [];
      if (!hist.length) continue;
      const last = hist[hist.length - 1];
      const bbox = Array.isArray(last.bbox) ? last.bbox : null;
      if (!bbox || bbox.length < 4) continue;
      const [x, y, bw, bh] = bbox.map((n) => Number(n));
      const sx = (x / dims.w) * w;
      const sy = (y / dims.h) * h;
      const sw = (bw / dims.w) * w;
      const sh = (bh / dims.h) * h;
      ctx.strokeRect(sx, sy, sw, sh);
    }
  }, [props.track, dims]);

  return (
    <div style={{ position: "relative" }}>
      <VideoViewer jpegBytes={props.jpegBytes} width={dims.w} height={dims.h} />
      <canvas
        ref={canvasRef}
        width={dims.w}
        height={dims.h}
        style={{ position: "absolute", left: 0, top: 0, width: "100%", height: "100%", pointerEvents: "none" }}
      />
    </div>
  );
}

