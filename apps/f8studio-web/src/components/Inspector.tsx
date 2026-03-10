import { useEffect, useMemo, useState } from "react";
import type { GraphNode, NodeVariantRecord } from "../types";
import { validateF8Schema, validateValueBySchema, stripNullsDeep } from "../studio/schema";

type TabKey = "properties" | "schema" | "variants" | "viz";

function asObjArray(v: unknown): Record<string, unknown>[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x && typeof x === "object") as Record<string, unknown>[];
}

function stateFields(spec: Record<string, unknown>): Record<string, unknown>[] {
  return asObjArray((spec as any).stateFields);
}

function dataPorts(spec: Record<string, unknown>, dir: "in" | "out"): Record<string, unknown>[] {
  const key = dir === "in" ? "dataInPorts" : "dataOutPorts";
  return asObjArray((spec as any)[key]);
}

function commands(spec: Record<string, unknown>): Record<string, unknown>[] {
  return asObjArray((spec as any).commands);
}

function jsonPretty(v: unknown): string {
  try {
    return JSON.stringify(v ?? {}, null, 2);
  } catch {
    return "{}";
  }
}

export function Inspector(props: {
  node: GraphNode;
  variants: NodeVariantRecord[];
  vizSummary: Record<string, unknown> | null;
  onPatchNode: (patch: (n: GraphNode) => GraphNode) => void;
  onSaveVariant: (record: NodeVariantRecord) => Promise<void>;
  onApplyVariant: (record: NodeVariantRecord) => void;
}) {
  const [tab, setTab] = useState<TabKey>("properties");
  const spec = props.node.spec ?? {};

  const baseVariants = useMemo(() => props.variants.filter((v) => v.baseNodeType === props.node.nodeType), [props.variants, props.node.nodeType]);

  const fields = useMemo(() => stateFields(spec), [spec]);

  const renderProperties = () => {
    return (
      <div>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Properties</div>

        {fields.map((f) => {
          const name = String(f.name ?? "").trim();
          if (!name) return null;
          const access = String(f.access ?? "").trim();
          const schema = f.valueSchema;
          const current = (props.node.state ?? {})[name];
          const t = String((schema as any)?.type ?? "any");

          const setValue = (v: unknown) => {
            const r = validateValueBySchema(schema, v);
            if (!r.ok) {
              alert(`${name}: ${r.error}`);
              return;
            }
            props.onPatchNode((n) => ({ ...n, state: { ...(n.state ?? {}), [name]: v } }));
          };

          if (access === "ro") {
            return (
              <div key={name} className="fieldRow">
                <label>
                  {name} <span className="muted">({access})</span>
                </label>
                <div className="mono" style={{ fontSize: 12, opacity: 0.85 }}>
                  {jsonPretty(current)}
                </div>
              </div>
            );
          }

          if (t === "boolean") {
            return (
              <div key={name} className="fieldRow">
                <label>{name}</label>
                <select value={String(Boolean(current))} onChange={(e) => setValue(e.target.value === "true")}>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              </div>
            );
          }

          if (t === "string") {
            const enumVals = Array.isArray((schema as any)?.enum) ? (schema as any).enum : null;
            if (enumVals && enumVals.length) {
              return (
                <div key={name} className="fieldRow">
                  <label>{name}</label>
                  <select value={String(current ?? "")} onChange={(e) => setValue(e.target.value)}>
                    {enumVals.map((x: any) => (
                      <option key={String(x)} value={String(x)}>
                        {String(x)}
                      </option>
                    ))}
                  </select>
                </div>
              );
            }
            return (
              <div key={name} className="fieldRow">
                <label>{name}</label>
                <input value={String(current ?? "")} onChange={(e) => setValue(e.target.value)} />
              </div>
            );
          }

          if (t === "number" || t === "integer") {
            return (
              <div key={name} className="fieldRow">
                <label>{name}</label>
                <input
                  value={current === undefined || current === null ? "" : String(current)}
                  onChange={(e) => {
                    const raw = e.target.value;
                    if (!raw.trim()) {
                      props.onPatchNode((n) => {
                        const next = { ...(n.state ?? {}) };
                        delete (next as any)[name];
                        return { ...n, state: next };
                      });
                      return;
                    }
                    const n = Number(raw);
                    if (Number.isNaN(n)) return;
                    setValue(t === "integer" ? Math.trunc(n) : n);
                  }}
                />
              </div>
            );
          }

          // object/array/any/null: edit as JSON.
          return (
            <JsonField
              key={name}
              label={name}
              value={current}
              onApply={(v) => setValue(v)}
            />
          );
        })}

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>custom</div>
          <JsonObjectEditor
            value={props.node.custom ?? {}}
            onApply={(obj) => props.onPatchNode((n) => ({ ...n, custom: obj }))}
          />
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>sys</div>
          <JsonObjectEditor
            value={props.node.sys ?? {}}
            onApply={(obj) => props.onPatchNode((n) => ({ ...n, sys: obj }))}
          />
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>uiOverrides</div>
          <JsonObjectEditor
            value={props.node.uiOverrides ?? {}}
            onApply={(obj) => props.onPatchNode((n) => ({ ...n, uiOverrides: obj }))}
          />
        </div>
      </div>
    );
  };

  const renderSchema = () => <SchemaEditor node={props.node} onPatchNode={props.onPatchNode} />;

  const renderVariants = () => {
    const save = async () => {
      const name = window.prompt("Variant name?");
      if (!name || !name.trim()) return;
      const now = new Date().toISOString();
      const specAny = props.node.spec ?? {};
      const serviceClass = String((specAny as any).serviceClass ?? "").trim();
      const operatorClass = String((specAny as any).operatorClass ?? "").trim();
      const record: NodeVariantRecord = {
        variantId: crypto.randomUUID(),
        kind: operatorClass ? "operator" : "service",
        baseNodeType: props.node.nodeType,
        serviceClass,
        operatorClass: operatorClass ? operatorClass : null,
        name: name.trim(),
        description: "",
        tags: [],
        spec: specAny,
        createdAt: now,
        updatedAt: now,
      };
      await props.onSaveVariant(record);
    };

    return (
      <div>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Variants</div>
        <div className="toolbar">
          <button className="btn" onClick={save}>
            Save Variant From Node
          </button>
        </div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
          baseNodeType={props.node.nodeType} variants={baseVariants.length}
        </div>
        <div className="paletteList">
          {baseVariants.map((v) => (
            <div key={v.variantId} className="paletteItem">
              <div style={{ fontWeight: 800, fontSize: 12 }}>{v.name}</div>
              <div className="mono" style={{ fontSize: 11, opacity: 0.7 }}>
                {v.variantId}
              </div>
              <div style={{ marginTop: 8 }}>
                <button className="btn" onClick={() => props.onApplyVariant(v)}>
                  Apply To Node
                </button>
              </div>
            </div>
          ))}
          {!baseVariants.length ? <div className="muted">No variants for this baseNodeType.</div> : null}
        </div>
      </div>
    );
  };

  const renderViz = () => {
    return (
      <div>
        <div style={{ fontWeight: 800, marginBottom: 10 }}>Viz</div>
        {props.vizSummary ? (
          <pre className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
            {jsonPretty(props.vizSummary)}
          </pre>
        ) : (
          <div className="muted">No viz commands for this node yet.</div>
        )}
      </div>
    );
  };

  return (
    <div>
      <div style={{ fontWeight: 900, marginBottom: 6 }}>Inspector</div>
      <div className="mono" style={{ fontSize: 12, opacity: 0.85, marginBottom: 10 }}>
        {props.node.id}
      </div>

      <div className="tabs">
        <button className={`tab ${tab === "properties" ? "active" : ""}`} onClick={() => setTab("properties")}>
          Properties
        </button>
        <button className={`tab ${tab === "schema" ? "active" : ""}`} onClick={() => setTab("schema")}>
          Schema
        </button>
        <button className={`tab ${tab === "variants" ? "active" : ""}`} onClick={() => setTab("variants")}>
          Variants
        </button>
        <button className={`tab ${tab === "viz" ? "active" : ""}`} onClick={() => setTab("viz")}>
          Viz
        </button>
      </div>

      {tab === "properties" ? renderProperties() : null}
      {tab === "schema" ? renderSchema() : null}
      {tab === "variants" ? renderVariants() : null}
      {tab === "viz" ? renderViz() : null}
    </div>
  );
}

function SchemaEditor(props: { node: GraphNode; onPatchNode: (patch: (n: GraphNode) => GraphNode) => void }) {
  const spec = props.node.spec ?? {};
  const [scope, setScope] = useState<string>("state");
  const [key, setKey] = useState<string>("");
  const [text, setText] = useState<string>("");
  const [err, setErr] = useState<string>("");

  const options = useMemo(() => {
    const out: { id: string; label: string; schema: unknown; apply: (schema: unknown) => void }[] = [];
    for (const f of stateFields(spec)) {
      const name = String(f.name ?? "").trim();
      if (!name) continue;
      out.push({
        id: `state:${name}`,
        label: `stateFields.${name}.valueSchema`,
        schema: f.valueSchema,
        apply: (schema2) => {
          props.onPatchNode((n) => {
            const nextSpec = { ...(n.spec ?? {}) } as any;
            const sfs = asObjArray(nextSpec.stateFields).map((x) => ({ ...x }));
            for (const it of sfs) {
              if (String(it.name ?? "").trim() === name) it.valueSchema = schema2;
            }
            nextSpec.stateFields = sfs;
            return { ...n, spec: nextSpec };
          });
        },
      });
    }
    for (const p of dataPorts(spec, "in")) {
      const name = String(p.name ?? "").trim();
      if (!name) continue;
      out.push({
        id: `dataIn:${name}`,
        label: `dataInPorts.${name}.valueSchema`,
        schema: p.valueSchema,
        apply: (schema2) => {
          props.onPatchNode((n) => {
            const nextSpec = { ...(n.spec ?? {}) } as any;
            const ps = asObjArray(nextSpec.dataInPorts).map((x) => ({ ...x }));
            for (const it of ps) {
              if (String(it.name ?? "").trim() === name) it.valueSchema = schema2;
            }
            nextSpec.dataInPorts = ps;
            return { ...n, spec: nextSpec };
          });
        },
      });
    }
    for (const p of dataPorts(spec, "out")) {
      const name = String(p.name ?? "").trim();
      if (!name) continue;
      out.push({
        id: `dataOut:${name}`,
        label: `dataOutPorts.${name}.valueSchema`,
        schema: p.valueSchema,
        apply: (schema2) => {
          props.onPatchNode((n) => {
            const nextSpec = { ...(n.spec ?? {}) } as any;
            const ps = asObjArray(nextSpec.dataOutPorts).map((x) => ({ ...x }));
            for (const it of ps) {
              if (String(it.name ?? "").trim() === name) it.valueSchema = schema2;
            }
            nextSpec.dataOutPorts = ps;
            return { ...n, spec: nextSpec };
          });
        },
      });
    }
    for (const c of commands(spec)) {
      const cname = String(c.name ?? "").trim();
      const params = asObjArray(c.params);
      for (const p of params) {
        const pname = String(p.name ?? "").trim();
        if (!cname || !pname) continue;
        out.push({
          id: `cmd:${cname}:${pname}`,
          label: `commands.${cname}.params.${pname}.valueSchema`,
          schema: p.valueSchema,
          apply: (schema2) => {
            props.onPatchNode((n) => {
              const nextSpec = { ...(n.spec ?? {}) } as any;
              const cs = asObjArray(nextSpec.commands).map((x) => ({ ...x }));
              for (const it of cs) {
                if (String(it.name ?? "").trim() !== cname) continue;
                const ps = asObjArray(it.params).map((x) => ({ ...x }));
                for (const jt of ps) {
                  if (String(jt.name ?? "").trim() === pname) jt.valueSchema = schema2;
                }
                it.params = ps;
              }
              nextSpec.commands = cs;
              return { ...n, spec: nextSpec };
            });
          },
        });
      }
    }
    return out;
  }, [spec, props.onPatchNode]);

  const selected = options.find((o) => o.id === key) ?? null;
  const scoped = options.filter((o) =>
    scope === "state" ? o.id.startsWith("state:") : scope === "data" ? o.id.startsWith("data") : o.id.startsWith("cmd:"),
  );

  const pick = (id: string) => {
    setKey(id);
    const o = options.find((x) => x.id === id);
    setText(o ? jsonPretty(o.schema) : "");
    setErr("");
  };

  const apply = () => {
    setErr("");
    let obj: unknown;
    try {
      obj = JSON.parse(text);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
      return;
    }
    const res = validateF8Schema(obj);
    if (!res.ok) {
      setErr(res.error);
      return;
    }
    if (!selected) return;
    selected.apply(res.value);
  };

  const normalize = () => {
    let obj: unknown;
    try {
      obj = JSON.parse(text);
    } catch {
      return;
    }
    const stripped = stripNullsDeep(obj);
    setText(jsonPretty(stripped));
  };

  return (
    <div>
      <div style={{ fontWeight: 800, marginBottom: 10 }}>Schema</div>
      <div className="fieldRow">
        <label>Scope</label>
        <select value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="state">stateFields</option>
          <option value="data">dataPorts</option>
          <option value="cmd">commands</option>
        </select>
      </div>
      <div className="fieldRow">
        <label>Field</label>
        <select value={key} onChange={(e) => pick(e.target.value)}>
          <option value="">(select)</option>
          {scoped.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="fieldRow">
        <label>valueSchema (JSON)</label>
        <textarea value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      {err ? (
        <pre className="mono" style={{ fontSize: 12, color: "#fecaca", whiteSpace: "pre-wrap" }}>
          {err}
        </pre>
      ) : null}
      <div className="toolbar">
        <button className="btn" onClick={apply} disabled={!selected}>
          Apply
        </button>
        <button className="btn" onClick={normalize} disabled={!text.trim()}>
          Normalize (strip nulls)
        </button>
      </div>
    </div>
  );
}

function JsonField(props: { label: string; value: unknown; onApply: (v: unknown) => void }) {
  const [text, setText] = useState<string>(jsonPretty(props.value));
  const [err, setErr] = useState<string>("");
  useEffect(() => {
    setText(jsonPretty(props.value));
    setErr("");
  }, [props.value]);
  const apply = () => {
    setErr("");
    let obj: unknown;
    try {
      obj = JSON.parse(text);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
      return;
    }
    props.onApply(obj);
  };
  return (
    <div className="fieldRow">
      <label>{props.label}</label>
      <textarea value={text} onChange={(e) => setText(e.target.value)} />
      {err ? (
        <pre className="mono" style={{ fontSize: 12, color: "#fecaca", whiteSpace: "pre-wrap" }}>
          {err}
        </pre>
      ) : null}
      <button className="btn" onClick={apply}>
        Apply
      </button>
    </div>
  );
}

function JsonObjectEditor(props: { value: Record<string, unknown>; onApply: (obj: Record<string, unknown>) => void }) {
  const [text, setText] = useState<string>(jsonPretty(props.value));
  const [err, setErr] = useState<string>("");
  useEffect(() => {
    setText(jsonPretty(props.value));
    setErr("");
  }, [props.value]);
  const apply = () => {
    setErr("");
    let obj: unknown;
    try {
      obj = JSON.parse(text);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
      return;
    }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
      setErr("must be a JSON object");
      return;
    }
    props.onApply(obj as Record<string, unknown>);
  };
  return (
    <div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} />
      {err ? (
        <pre className="mono" style={{ fontSize: 12, color: "#fecaca", whiteSpace: "pre-wrap" }}>
          {err}
        </pre>
      ) : null}
      <button className="btn" onClick={apply}>
        Apply
      </button>
    </div>
  );
}
