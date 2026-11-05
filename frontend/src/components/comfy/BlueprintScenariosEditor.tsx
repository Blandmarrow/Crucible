import type { BlueprintCombineItem, BlueprintScenario, BlueprintSpec } from "../../api/comfy";
import NumberField from "../common/NumberField";
import BlueprintSection, { type SectionControl, type SectionRef } from "./BlueprintSection";
import { BLUEPRINT_LIMITS, defaultPath, defaultTemplate, freshName } from "./blueprintKeys";

const NEW_SCENARIO: BlueprintScenario = { name: "", combine: [], mode: "full", count: 1, path: "", template: "", rewrite: false };

const axisOf = (c: BlueprintCombineItem) => (typeof c === "string" ? c : c.axis);
const takeOf = (c: BlueprintCombineItem) => (typeof c === "string" ? 1 : c.take);
const makeItem = (axis: string, take: number): BlueprintCombineItem => (take > 1 ? { axis, take } : axis);
// The server's bounds (schemas/comfy_blueprint.py): beyond them a save and the preview 422.
const bounded = (max: number) => (n: number) => Math.min(max, Math.max(1, Math.round(n)));
const clampK = bounded(100_000);
const clampCount = bounded(10_000);
const clampTake = bounded(4);

function sampleK(mode: string): number | null {
  const m = /^sample:(\d+)$/.exec(mode);
  return m ? Number(m[1]) : null;
}

interface Props {
  spec: BlueprintSpec;
  onChange: (scenarios: BlueprintScenario[]) => void;
  section: SectionControl;
  ref?: SectionRef;
}

const smallLabel = { fontSize: 12, color: "var(--fg-mute)", display: "flex", alignItems: "center", gap: 6 } as const;

export default function BlueprintScenariosEditor({ spec, onChange, section, ref }: Props) {
  const { scenarios } = spec;
  const axisNames = Object.keys(spec.axes);
  const placeholders = [
    ...axisNames.flatMap((a) => [`{${a}}`, `{${a}.label}`]),
    ...Object.keys(spec.variations).map((v) => `{${v}}`),
  ];

  const patch = (i: number, p: Partial<BlueprintScenario>) =>
    onChange(scenarios.map((s, j) => (j === i ? { ...s, ...p } : s)));

  return (
    <BlueprintSection
      ref={ref} title="Scenarios" summary={`${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"}`} control={section}
      action={
        <button className="btn ghost sm" disabled={scenarios.length >= BLUEPRINT_LIMITS.scenarios}
          title={scenarios.length >= BLUEPRINT_LIMITS.scenarios ? `A blueprint holds at most ${BLUEPRINT_LIMITS.scenarios} scenarios` : undefined}
          onClick={() => {
            section.setOpen(true);
            onChange([...scenarios, { ...NEW_SCENARIO, name: freshName(Object.fromEntries(scenarios.map((x) => [x.name, true])), "scenario") }]);
          }}>
          + Add scenario
        </button>
      }
    >
        {scenarios.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            A scenario combines axes into folders: one folder for each combination. It says how many
            images each folder holds and how its prompts read.
          </p>
        )}
        {scenarios.map((s, i) => {
          const k = sampleK(s.mode);
          return (
            <div key={i} data-testid={`scenario-${i}`}
              style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <input className="input" style={{ width: 180, height: 30, fontSize: 12 }}
                  aria-label="Scenario name" maxLength={BLUEPRINT_LIMITS.scenarioName} placeholder="Scenario name"
                  value={s.name} onChange={(e) => patch(i, { name: e.target.value })} />
                <label style={smallLabel}>
                  Mode
                  <select className="select" style={{ height: 30, fontSize: 12 }} aria-label="Scenario mode"
                    value={k === null ? "full" : "sample"}
                    onChange={(e) => patch(i, { mode: e.target.value === "full" ? "full" : `sample:${k ?? 1}` })}>
                    <option value="full">Every combination</option>
                    <option value="sample">Sample</option>
                  </select>
                </label>
                {k !== null && (
                  <label style={smallLabel}>
                    Folders
                    <NumberField className="input" style={{ width: 70, height: 30, fontSize: 12 }}
                      aria-label="Sample size" min={1} value={k} clamp={clampK}
                      onCommit={(n) => patch(i, { mode: `sample:${n}` })} />
                  </label>
                )}
                <label style={smallLabel}>
                  Images per folder
                  <NumberField className="input" style={{ width: 70, height: 30, fontSize: 12 }}
                    aria-label="Images per folder" min={1} max={10000} value={s.count} clamp={clampCount}
                    onCommit={(n) => patch(i, { count: n })} />
                </label>
                <div style={{ flex: 1 }} />
                <button className="btn ghost sm" style={{ color: "var(--bad)" }} aria-label={`Remove scenario ${s.name}`}
                  onClick={() => onChange(scenarios.filter((_, j) => j !== i))}>Remove scenario</button>
              </div>

              <div>
                <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 4 }}>
                  Combine (take more than 1 for shots with several together)
                </div>
                {s.combine.map((c, ci) => {
                  const axis = axisOf(c);
                  const options = axisNames.includes(axis) ? axisNames : [axis, ...axisNames];
                  const setItem = (item: BlueprintCombineItem) =>
                    patch(i, { combine: s.combine.map((x, j) => (j === ci ? item : x)) });
                  return (
                    <div key={ci} style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                      <select className="select" style={{ height: 30, fontSize: 12 }} aria-label="Combine axis"
                        value={axis} onChange={(e) => setItem(makeItem(e.target.value, takeOf(c)))}>
                        {options.map((a) => <option key={a} value={a}>{a}</option>)}
                      </select>
                      <label style={smallLabel}>
                        take
                        <NumberField className="input" style={{ width: 60, height: 30, fontSize: 12 }}
                          aria-label="Take" min={1} max={4} value={takeOf(c)} clamp={clampTake}
                          onCommit={(n) => setItem(makeItem(axis, n))} />
                      </label>
                      <button className="btn ghost sm" aria-label="Remove combined axis"
                        onClick={() => patch(i, { combine: s.combine.filter((_, j) => j !== ci) })}>×</button>
                    </div>
                  );
                })}
                <button className="btn ghost sm" disabled={axisNames.length === 0 || s.combine.length >= BLUEPRINT_LIMITS.combineItems}
                  title={axisNames.length === 0 ? "Add an axis first"
                    : s.combine.length >= BLUEPRINT_LIMITS.combineItems ? `A scenario combines at most ${BLUEPRINT_LIMITS.combineItems} axes` : undefined}
                  onClick={() => patch(i, { combine: [...s.combine, axisNames[0]] })}>+ Combine an axis</button>
              </div>

              {/* Blank uses the default, shown as the placeholder so the user sees what
                  they get; the server computes the same (blueprint_expand.default_*). */}
              <label style={{ ...smallLabel, flexDirection: "column", alignItems: "stretch" }}>
                Folder path <span style={{ fontWeight: 400 }}>(blank: one folder level per axis)</span>
                <input className="input mono" style={{ height: 30, fontSize: 12 }}
                  aria-label="Folder path" maxLength={BLUEPRINT_LIMITS.path}
                  placeholder={defaultPath(s.combine.map(axisOf)) || "{biome}/{character}"}
                  value={s.path} onChange={(e) => patch(i, { path: e.target.value })} />
              </label>
              <label style={{ ...smallLabel, flexDirection: "column", alignItems: "stretch" }}>
                Prompt template <span style={{ fontWeight: 400 }}>(blank: every axis, then every variation)</span>
                <textarea className="input" style={{ minHeight: 50, fontSize: 12, resize: "vertical" }}
                  aria-label="Prompt template" maxLength={BLUEPRINT_LIMITS.template}
                  placeholder={defaultTemplate(s.combine.map(axisOf), Object.keys(spec.variations))
                    || "{character}, standing in {biome}, {camera}"}
                  value={s.template ?? ""} onChange={(e) => patch(i, { template: e.target.value })} />
              </label>
              <label style={{ ...smallLabel, cursor: "pointer" }}
                title="Optional. After Apply, an LLM rewrites this scenario's template prompts into prose. It costs LLM calls and you confirm the budget first.">
                <input type="checkbox" className="checkbox" aria-label="Rewrite into prose (LLM)"
                  checked={!!s.rewrite} onChange={(e) => patch(i, { rewrite: e.target.checked })} />
                Rewrite into prose (LLM) <span style={{ fontWeight: 400 }}>(optional, costs LLM calls)</span>
              </label>
              <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>
                Placeholders: <span className="mono">{"{axis}"}</span> (descriptor),{" "}
                <span className="mono">{"{axis.label}"}</span>, <span className="mono">{"{variation}"}</span>.
                {placeholders.length > 0 && <> Here: <span className="mono">{placeholders.join(" ")}</span></>}
              </div>
            </div>
          );
        })}
    </BlueprintSection>
  );
}
