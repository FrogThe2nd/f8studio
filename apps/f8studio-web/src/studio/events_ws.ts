import type { EventsWsMessage } from "../types";

export class EventsWsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private onMsg: (m: EventsWsMessage) => void;
  private onState: (s: { connected: boolean }) => void;

  constructor(args: { url: string; onMsg: (m: EventsWsMessage) => void; onState: (s: { connected: boolean }) => void }) {
    this.url = args.url;
    this.onMsg = args.onMsg;
    this.onState = args.onState;
  }

  connect() {
    this.close();
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => this.onState({ connected: true });
    ws.onclose = () => this.onState({ connected: false });
    ws.onerror = () => this.onState({ connected: false });
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      try {
        const obj = JSON.parse(ev.data);
        if (!obj || typeof obj !== "object") return;
        this.onMsg(obj as EventsWsMessage);
      } catch {
        return;
      }
    };
  }

  send(obj: Record<string, unknown>) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(obj));
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

