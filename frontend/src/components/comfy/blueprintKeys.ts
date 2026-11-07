import type { BlueprintSpec } from "../../api/comfy";

/** Rebuild a record with one key renamed, keeping its position. */
export function renameKey<T>(rec: Record<string, T>, from: string, to: string): Record<string, T> {
  return Object.fromEntries(Object.entries(rec).map(([k, v]) => [k === from ? to : k, v]));
}

/** Rename an axis or variation across the whole spec: the key itself, and every
 *  scenario's `combine`, path and template reference to it (`{name}`, `{name.label}`).
 *  Without this, a rename turns each reference into a validation error. */
export function renameRefs(spec: BlueprintSpec, kind: "axes" | "variations", from: string, to: string): BlueprintSpec {
  const escaped = from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const ref = new RegExp(`\\{${escaped}(\\.label)?\\}`, "g");
  const swap = (text: string) => text.replace(ref, (_m, attr?: string) => `{${to}${attr ?? ""}}`);
  const scenarios = spec.scenarios.map((s) => ({
    ...s,
    combine: kind === "axes"
      ? s.combine.map((c) => (typeof c === "string" ? (c === from ? to : c) : c.axis === from ? { ...c, axis: to } : c))
      : s.combine,
    path: kind === "axes" ? swap(s.path) : s.path,
    template: s.template === null ? null : swap(s.template),
  }));
  return kind === "axes"
    ? { ...spec, axes: renameKey(spec.axes, from, to), scenarios }
    : { ...spec, variations: renameKey(spec.variations, from, to), scenarios };
}

/** What a blank folder path means: one level per combined axis, in combine order.
 *  Mirrors `default_path` in backend/services/blueprint_expand.py; keep them in step. */
export function defaultPath(axes: string[]): string {
  return axes.map((a) => `{${a}}`).join("/");
}

/** What a blank template means: every combined axis, then every variation. Mirrors
 *  `default_template` in backend/services/blueprint_expand.py. */
export function defaultTemplate(axes: string[], variations: string[]): string {
  return [...axes, ...variations].map((n) => `{${n}}`).join(", ");
}

/** An unused `base<N>` key. */
export function freshName(rec: Record<string, unknown>, base: string): string {
  let n = Object.keys(rec).length + 1;
  while (`${base}${n}` in rec) n++;
  return `${base}${n}`;
}

/** Length limits the server enforces on a blueprint (backend/schemas/comfy_blueprint.py).
 *  Past them a save 422s and the autosave stops, so the inputs stop typing at them. */
export const BLUEPRINT_LIMITS = {
  name: 40,
  scenarioName: 100,
  path: 400,
  template: 4000,
  label: 200,
  descriptor: 2000,
  piece: 300,
  // List sizes, from the same schema: past any of them the save 422s too.
  axes: 8,
  entitiesPerAxis: 200,
  variations: 12,
  piecesPerVariation: 100,
  scenarios: 50,
  combineItems: 6,
} as const;
