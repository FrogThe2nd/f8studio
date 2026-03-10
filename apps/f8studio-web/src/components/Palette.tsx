import { useMemo, useState } from "react";
import type { NodeVariantRecord } from "../types";

export type PaletteItem =
  | { kind: "service"; spec: Record<string, unknown> }
  | { kind: "operator"; spec: Record<string, unknown> }
  | { kind: "variant"; record: NodeVariantRecord };

function isContainerServiceSpec(spec: Record<string, unknown>): boolean {
  return String((spec as any).rendererClass ?? "").trim() === "default_container";
}

function labelForSpec(spec: Record<string, unknown>): string {
  const label = String((spec as any).label ?? "").trim();
  if (label) return label;
  const op = String((spec as any).operatorClass ?? "").trim();
  const svc = String((spec as any).serviceClass ?? "").trim();
  return op ? `${op}` : svc ? `${svc}` : "Unnamed";
}

function hayForSpec(spec: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const k of ["label", "serviceClass", "operatorClass", "description", "version", "rendererClass"]) {
    const v = String((spec as any)[k] ?? "").trim();
    if (v) parts.push(v);
  }
  const tags = (spec as any).tags;
  if (Array.isArray(tags)) {
    for (const t of tags) parts.push(String(t));
  }
  return parts.join(" ").toLowerCase();
}

function hayForVariant(v: NodeVariantRecord): string {
  return [v.name, v.baseNodeType, v.serviceClass, v.operatorClass ?? "", v.description, ...(v.tags ?? [])]
    .join(" ")
    .toLowerCase();
}

export function Palette(props: {
  services: Record<string, unknown>[];
  operators: Record<string, unknown>[];
  variants: NodeVariantRecord[];
  onPick: (item: PaletteItem) => void;
  onDragStart: (ev: React.DragEvent, item: PaletteItem) => void;
  searchInputRef?: React.RefObject<HTMLInputElement>;
}) {
  const [q, setQ] = useState<string>("");
  const qq = q.trim().toLowerCase();

  const filtered = useMemo(() => {
    const servicesAll = props.services
      .map((spec) => ({ kind: "service" as const, spec }))
      .filter((x) => (qq ? hayForSpec(x.spec).includes(qq) : true));
    const containerServices = servicesAll.filter((x) => isContainerServiceSpec(x.spec));
    const standaloneServices = servicesAll.filter((x) => !isContainerServiceSpec(x.spec));
    const operators = props.operators
      .map((spec) => ({ kind: "operator" as const, spec }))
      .filter((x) => (qq ? hayForSpec(x.spec).includes(qq) : true));
    const variants = props.variants
      .map((record) => ({ kind: "variant" as const, record }))
      .filter((x) => (qq ? hayForVariant(x.record).includes(qq) : true));
    return { containerServices, standaloneServices, operators, variants };
  }, [props.services, props.operators, props.variants, qq]);

  const renderItem = (item: PaletteItem) => {
    const key =
      item.kind === "variant"
        ? `variant:${item.record.variantId}`
        : item.kind === "service"
          ? `svc:${String((item.spec as any).serviceClass ?? "")}`
          : `op:${String((item.spec as any).serviceClass ?? "")}:${String((item.spec as any).operatorClass ?? "")}`;
    const title = item.kind === "variant" ? item.record.name : labelForSpec(item.spec);
    const subtitle =
      item.kind === "variant"
        ? `${item.record.baseNodeType}`
        : item.kind === "service"
          ? `svc.${String((item.spec as any).serviceClass ?? "")}`
          : `${String((item.spec as any).serviceClass ?? "")}.${String((item.spec as any).operatorClass ?? "")}`;

    const badge =
      item.kind === "service"
        ? isContainerServiceSpec(item.spec)
          ? "container"
          : "service"
        : item.kind === "operator"
          ? "operator"
          : item.record.kind === "service"
            ? isContainerServiceSpec(item.record.spec)
              ? "variant:container"
              : "variant:service"
            : "variant:operator";

    return (
      <div
        key={key}
        className="paletteItem"
        draggable
        onDragStart={(ev) => props.onDragStart(ev, item)}
        onDoubleClick={() => props.onPick(item)}
        onClick={() => props.onPick(item)}
        title={subtitle}
      >
        <div style={{ fontWeight: 700, fontSize: 12, display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10 }}>
          <span>{title}</span>
          <span className="mono" style={{ fontSize: 10, opacity: 0.7 }}>
            {badge}
          </span>
        </div>
        <div className="mono" style={{ fontSize: 11, opacity: 0.7 }}>
          {subtitle}
        </div>
      </div>
    );
  };

  return (
    <div>
      <div style={{ fontWeight: 800, marginBottom: 8 }}>Palette</div>
      <input
        ref={props.searchInputRef}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search services/operators/variants"
      />

      <div style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontWeight: 700, marginBottom: 6 }}>
          Container Services ({filtered.containerServices.length})
        </div>
        <div className="paletteList">{filtered.containerServices.slice(0, 50).map(renderItem)}</div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontWeight: 700, marginBottom: 6 }}>
          Standalone Services ({filtered.standaloneServices.length})
        </div>
        <div className="paletteList">{filtered.standaloneServices.slice(0, 50).map(renderItem)}</div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontWeight: 700, marginBottom: 6 }}>
          Operators ({filtered.operators.length})
        </div>
        <div className="paletteList">{filtered.operators.slice(0, 80).map(renderItem)}</div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontWeight: 700, marginBottom: 6 }}>
          Variants ({filtered.variants.length})
        </div>
        <div className="paletteList">{filtered.variants.slice(0, 80).map(renderItem)}</div>
      </div>
    </div>
  );
}
