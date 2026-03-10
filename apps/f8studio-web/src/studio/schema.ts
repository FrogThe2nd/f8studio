import { z } from "zod";

// Tolerate legacy payloads that include keys with null values by stripping them before validation.
export function stripNullsDeep(value: unknown): unknown {
  if (value === null) return undefined;
  if (Array.isArray(value)) return value.map(stripNullsDeep).filter((v) => v !== undefined);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const vv = stripNullsDeep(v);
      if (vv !== undefined) out[k] = vv;
    }
    return out;
  }
  return value;
}

const common = {
  title: z.string().optional(),
  description: z.string().optional(),
  default: z.any().optional(),
  examples: z.array(z.any()).optional(),
  "$comment": z.string().optional(),
  field_comment: z.string().optional(),
};

const stringSchema = z
  .object({ ...common, type: z.literal("string"), enum: z.array(z.any()).optional() })
  .strict();

const numberSchema = z
  .object({
    ...common,
    type: z.literal("number"),
    enum: z.array(z.any()).optional(),
    minimum: z.number().optional(),
    maximum: z.number().optional(),
    exclusiveMinimum: z.number().optional(),
    exclusiveMaximum: z.number().optional(),
    multipleOf: z.number().positive().optional(),
  })
  .strict();

const integerSchema = z
  .object({
    ...common,
    type: z.literal("integer"),
    enum: z.array(z.any()).optional(),
    minimum: z.number().optional(),
    maximum: z.number().optional(),
    exclusiveMinimum: z.number().optional(),
    exclusiveMaximum: z.number().optional(),
    multipleOf: z.number().positive().optional(),
  })
  .strict();

const booleanSchema = z
  .object({ ...common, type: z.literal("boolean"), enum: z.array(z.any()).optional() })
  .strict();

const nullSchema = z
  .object({ ...common, type: z.literal("null"), enum: z.array(z.any()).optional() })
  .strict();

const anySchema = z.object({ ...common, type: z.literal("any") }).strict();

let f8Schema: z.ZodTypeAny;

const objectSchema = z
  .object({
    ...common,
    type: z.literal("object"),
    properties: z.record(z.lazy(() => f8Schema)),
    required: z.array(z.string()).optional(),
    additionalProperties: z.boolean().optional(),
  })
  .strict();

const arraySchema = z
  .object({
    ...common,
    type: z.literal("array"),
    items: z.lazy(() => f8Schema),
  })
  .strict();

f8Schema = z.lazy(() =>
  z.discriminatedUnion("type", [stringSchema, numberSchema, integerSchema, booleanSchema, nullSchema, anySchema, objectSchema, arraySchema] as const),
);

export function validateF8Schema(input: unknown): { ok: true; value: any } | { ok: false; error: string } {
  const stripped = stripNullsDeep(input);
  const parsed = f8Schema.safeParse(stripped);
  if (!parsed.success) {
    return { ok: false, error: parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("\n") };
  }
  return { ok: true, value: parsed.data };
}

export function validateValueBySchema(schemaInput: unknown, value: unknown): { ok: true } | { ok: false; error: string } {
  const sRes = validateF8Schema(schemaInput);
  if (!sRes.ok) return { ok: false, error: `invalid valueSchema: ${sRes.error}` };
  const schema = sRes.value as any;
  const t = String(schema.type ?? "");

  const enumVals: unknown[] | null = Array.isArray(schema.enum) ? schema.enum : null;
  if (enumVals && enumVals.length) {
    const hit = enumVals.some((x) => JSON.stringify(x) === JSON.stringify(value));
    if (!hit) return { ok: false, error: "value not in enum" };
  }

  if (t === "any") return { ok: true };
  if (t === "null") return value === null ? { ok: true } : { ok: false, error: "expected null" };
  if (t === "boolean") return typeof value === "boolean" ? { ok: true } : { ok: false, error: "expected boolean" };
  if (t === "string") return typeof value === "string" ? { ok: true } : { ok: false, error: "expected string" };
  if (t === "number") return typeof value === "number" ? { ok: true } : { ok: false, error: "expected number" };
  if (t === "integer") return Number.isInteger(value) ? { ok: true } : { ok: false, error: "expected integer" };

  if (t === "array") {
    if (!Array.isArray(value)) return { ok: false, error: "expected array" };
    for (let i = 0; i < value.length; i += 1) {
      const r = validateValueBySchema(schema.items, value[i]);
      if (!r.ok) return { ok: false, error: `items[${i}]: ${r.error}` };
    }
    return { ok: true };
  }

  if (t === "object") {
    if (!value || typeof value !== "object" || Array.isArray(value)) return { ok: false, error: "expected object" };
    const obj = value as Record<string, unknown>;
    const props = schema.properties ?? {};
    const required: string[] = Array.isArray(schema.required) ? schema.required : [];
    for (const k of required) {
      if (!(k in obj)) return { ok: false, error: `missing required: ${k}` };
    }
    const additional = schema.additionalProperties;
    if (additional === false) {
      for (const k of Object.keys(obj)) {
        if (!(k in props)) return { ok: false, error: `additionalProperties not allowed: ${k}` };
      }
    }
    for (const [k, s2] of Object.entries(props)) {
      if (!(k in obj)) continue;
      const r = validateValueBySchema(s2, obj[k]);
      if (!r.ok) return { ok: false, error: `${k}: ${r.error}` };
    }
    return { ok: true };
  }

  return { ok: false, error: `unsupported schema type: ${t}` };
}
