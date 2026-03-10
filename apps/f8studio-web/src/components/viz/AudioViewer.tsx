import { useEffect, useRef } from "react";

export function AudioViewer(props: { samples: number[] | null }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const w = c.width;
    const h = c.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(96,165,250,0.95)";
    ctx.lineWidth = 1.5;

    const s = props.samples ?? [];
    if (!s.length) return;
    const mid = h / 2;
    ctx.beginPath();
    for (let i = 0; i < s.length; i += 1) {
      const x = (i / (s.length - 1)) * w;
      const y = mid - s[i] * (h * 0.45);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }, [props.samples]);

  return (
    <canvas
      ref={canvasRef}
      width={520}
      height={160}
      style={{ width: "100%", borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)" }}
    />
  );
}

