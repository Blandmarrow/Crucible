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

/** An unused `base<N>` key. */
export function freshName(rec: Record<string, unknown>, base: string): string {
  let n = Object.keys(rec).length + 1;
  while (`${base}${n}` in rec) n++;
  return `${base}${n}`;
}
