import { useEffect, useMemo, useRef, useState } from "react";

export function VideoViewer(props: { jpegBytes: Uint8Array | null; width?: number; height?: number }) {
  const [url, setUrl] = useState<string>("");
  const lastUrl = useRef<string>("");

  useEffect(() => {
    const bytes = props.jpegBytes;
    if (!bytes || !bytes.length) return;
    // Ensure ArrayBuffer-backed view for TS/DOM lib compatibility.
    const copy = new Uint8Array(bytes.byteLength);
    copy.set(bytes);
    const blob = new Blob([copy.buffer], { type: "image/jpeg" });
    const next = URL.createObjectURL(blob);
    const prev = lastUrl.current;
    lastUrl.current = next;
    setUrl(next);
    if (prev) URL.revokeObjectURL(prev);
  }, [props.jpegBytes]);

  const style = useMemo(() => {
    const w = props.width ? Number(props.width) : undefined;
    const h = props.height ? Number(props.height) : undefined;
    return {
      width: w ? `${w}px` : "100%",
      height: h ? `${h}px` : "auto",
      maxWidth: "100%",
      borderRadius: 12,
      border: "1px solid rgba(255,255,255,0.12)",
      background: "rgba(0,0,0,0.35)",
      display: "block",
    } as const;
  }, [props.width, props.height]);

  return url ? <img src={url} style={style} /> : <div className="muted">No frames yet.</div>;
}
