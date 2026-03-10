export type VizKind = "video" | "audio";

export type VizMeta = Record<string, unknown> & { subId: string; kind: string; mime?: string };

export type VizFrame = { meta: VizMeta; payload: Uint8Array };

export class VizWsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private onFrame: (f: VizFrame) => void;
  private onState: (s: { connected: boolean }) => void;

  constructor(args: { url: string; onFrame: (f: VizFrame) => void; onState: (s: { connected: boolean }) => void }) {
    this.url = args.url;
    this.onFrame = args.onFrame;
    this.onState = args.onState;
  }

  connect() {
    this.close();
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => this.onState({ connected: true });
    ws.onclose = () => this.onState({ connected: false });
    ws.onerror = () => this.onState({ connected: false });
    ws.onmessage = (ev) => {
      if (!(ev.data instanceof ArrayBuffer)) return;
      const buf = new Uint8Array(ev.data);
      if (buf.length < 4) return;
      const metaLen = buf[0] | (buf[1] << 8) | (buf[2] << 16) | (buf[3] << 24);
      if (metaLen <= 0 || buf.length < 4 + metaLen) return;
      const metaBytes = buf.slice(4, 4 + metaLen);
      const payload = buf.slice(4 + metaLen);
      try {
        const metaStr = new TextDecoder("utf-8").decode(metaBytes);
        const metaObj = JSON.parse(metaStr);
        if (!metaObj || typeof metaObj !== "object") return;
        this.onFrame({ meta: metaObj as any, payload });
      } catch {
        return;
      }
    };
  }

  sub(args: { subId: string; kind: VizKind; shmName: string; throttleMs?: number; historyMs?: number; channel?: number }) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(
      JSON.stringify({
        type: "sub",
        subId: args.subId,
        kind: args.kind,
        shmName: args.shmName,
        throttleMs: args.throttleMs ?? 33,
        historyMs: args.historyMs,
        channel: args.channel,
      }),
    );
  }

  unsub(subId: string) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "unsub", subId }));
  }

  close() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    try {
      ws.close();
    } catch {
      return;
    }
  }
}

