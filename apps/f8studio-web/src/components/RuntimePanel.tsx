import { useMemo, useState } from "react";
import type { GraphDoc, RuntimeStatus } from "../types";
import { compileGraph, deployRuntime } from "../api";

export interface LogLine {
  tsMs: number;
  level: string;
  context: string;
  message: string;
  excType?: string;
}

export function RuntimePanel(props: {
  doc: GraphDoc;
  runtime: RuntimeStatus | null;
  natsUrl: string;
  serviceIds: string[];
  defaultServiceId: string;
  onRequestStart: () => void;
  onRequestStop: () => void;
  logs: LogLine[];
  monitor: Record<string, unknown> | null;
  stateCount: number;
}) {
  const [deployServiceId, setDeployServiceId] = useState<string>(props.defaultServiceId);
  const [busy, setBusy] = useState<string>("");
  const [compileOut, setCompileOut] = useState<any>(null);
  const [err, setErr] = useState<string>("");

  const statusLine = useMemo(() => {
    if (!props.runtime) return "runtime: unknown";
    if (props.runtime.blocked) return "runtime: blocked by singleton guard";
    return props.runtime.running ? "runtime: running" : "runtime: stopped";
  }, [props.runtime]);

  const doCompile = async () => {
    setErr("");
    setBusy("Compiling...");
    try {
      const out = await compileGraph(props.doc);
      setCompileOut(out.compiled);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  };

  const doDeploy = async () => {
    setErr("");
    setBusy("Deploying...");
    try {
      await deployRuntime({ serviceId: deployServiceId, natsUrl: props.natsUrl, doc: props.doc });
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy("");
    }
  };

  return (
    <div>
      <div style={{ fontWeight: 800, marginBottom: 8 }}>Runtime</div>
      <div className="muted mono" style={{ fontSize: 12, marginBottom: 10 }}>
        {statusLine}
      </div>

      <div className="toolbar">
        <button className="btn" onClick={props.onRequestStart} disabled={!!props.runtime?.running}>
          Start
        </button>
        <button className="btn" onClick={props.onRequestStop} disabled={!props.runtime?.running}>
          Stop
        </button>
        <button className="btn" onClick={doCompile}>
          Compile
        </button>
      </div>

      <div className="fieldRow">
        <label>Deploy Target serviceId</label>
        <select value={deployServiceId} onChange={(e) => setDeployServiceId(e.target.value)}>
          {props.serviceIds.map((sid) => (
            <option key={sid} value={sid}>
              {sid}
            </option>
          ))}
        </select>
        <button className="btn" onClick={doDeploy} disabled={!deployServiceId}>
          Deploy
        </button>
      </div>

      {busy ? (
        <div className="muted" style={{ marginBottom: 10 }}>
          {busy}
        </div>
      ) : null}
      {err ? (
        <pre className="mono" style={{ fontSize: 12, color: "#fecaca", whiteSpace: "pre-wrap" }}>
          {err}
        </pre>
      ) : null}

      {compileOut ? (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>Compile</div>
          <div className="mono" style={{ fontSize: 12, opacity: 0.85 }}>
            per_service={Object.keys(compileOut.per_service ?? {}).length} warnings={(compileOut.warnings ?? []).length}
          </div>
          {(compileOut.warnings ?? []).length ? (
            <pre className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap", opacity: 0.85 }}>
              {(compileOut.warnings ?? []).join("\n")}
            </pre>
          ) : null}
        </div>
      ) : null}

      <div style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>State/Monitor</div>
        <div className="mono" style={{ fontSize: 12, opacity: 0.85 }}>
          stateKeys={props.stateCount} monitor={props.monitor ? "yes" : "no"}
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Logs</div>
        <div className="mono" style={{ fontSize: 11, maxHeight: 260, overflow: "auto" }}>
          {props.logs.slice(-120).map((l, idx) => (
            <div key={idx} style={{ opacity: 0.9 }}>
              [{new Date(l.tsMs).toLocaleTimeString()}] {l.level} {l.context}: {l.message}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

