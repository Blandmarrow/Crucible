import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ModalShell from "../common/ModalShell";
import { datasetsApi } from "../../api/datasets";
import { comfyApi, type BlueprintValue, type ComfyPlan } from "../../api/comfy";

type Props = {
  /** The page's dataset; the dataset picker starts on it. */
  datasetId: string;
  /** The plan being filled, left out of the choice. */
  planId: string;
  onClose: () => void;
} & (
  | {
      kind: "workflow";
      /** `withPins`: also take the source's pins and output settings, which name its node ids. */
      onCopy: (source: ComfyPlan, withPins: boolean) => void;
    }
  | {
      kind: "blueprint";
      /** Whether this plan's blueprint has anything in it, so the button says it replaces it. */
      replacing: boolean;
      onCopy: (value: BlueprintValue, sourceName: string) => void;
    }
);

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Copy from plan…: take another plan's workflow (Workflow tab) or blueprint (Blueprint
 *  tab), from any dataset. Reads the source through the ordinary GET endpoints; the
 *  caller decides what a copy does to its own form. */
export default function CopyFromPlanModal(props: Props) {
  const { datasetId, planId, onClose } = props;
  const [sourceDatasetId, setSourceDatasetId] = useState(datasetId);
  const [sourcePlanId, setSourcePlanId] = useState("");
  const [withPins, setWithPins] = useState(true);

  const { data: datasets = [] } = useQuery({ queryKey: ["datasets"], queryFn: () => datasetsApi.list() });
  const { data: plans = [], isLoading: plansLoading } = useQuery({
    queryKey: ["comfy", "plans", sourceDatasetId],
    queryFn: () => comfyApi.listPlans(sourceDatasetId),
    enabled: !!sourceDatasetId,
  });
  const eligible = useMemo(() => plans.filter((p) => p.id !== planId), [plans, planId]);
  // Falls back to the first other plan, derived rather than an effect.
  const srcId = eligible.some((p) => p.id === sourcePlanId) ? sourcePlanId : eligible[0]?.id ?? "";
  const srcName = eligible.find((p) => p.id === srcId)?.name ?? "";

  const planQuery = useQuery({
    queryKey: ["comfy", "plan", srcId],
    queryFn: () => comfyApi.getPlan(srcId),
    enabled: props.kind === "workflow" && !!srcId,
  });
  // Its own key, not the Blueprint tab's ["comfy", "blueprint", id]: that gate latches its
  // first value, and a read from here must not become the value a later visit opens on.
  const bpQuery = useQuery({
    queryKey: ["comfy", "blueprint-source", srcId],
    queryFn: () => comfyApi.getBlueprint(srcId),
    enabled: props.kind === "blueprint" && !!srcId,
    staleTime: 0,
  });

  const loading = props.kind === "workflow" ? planQuery.isLoading : bpQuery.isLoading;
  const failed = props.kind === "workflow" ? planQuery.isError : bpQuery.isError;

  let summary: string | null = null;
  let canCopy = false;
  if (props.kind === "workflow" && planQuery.data) {
    const p = planQuery.data;
    const nodes = Object.keys(p.workflow_json ?? {}).length;
    canCopy = nodes > 0;
    const prompt = p.pinned_params.find((x) => x.is_prompt);
    summary = nodes === 0
      ? "This plan has no workflow yet."
      : `${plural(nodes, "node")}, ${plural(p.pinned_params.length, "pinned parameter")}`
        + (prompt ? `, prompt pin "${prompt.alias}"` : ", no prompt pin") + ".";
  } else if (props.kind === "blueprint" && bpQuery.isSuccess) {
    const bp = bpQuery.data;
    const spec = bp?.spec;
    const axes = spec ? Object.keys(spec.axes ?? {}).length : 0;
    const variations = spec ? Object.keys(spec.variations ?? {}).length : 0;
    const scenarios = spec?.scenarios?.length ?? 0;
    canCopy = axes + variations + scenarios > 0;
    summary = !canCopy
      ? "This plan has no blueprint yet."
      : `${axes} ${axes === 1 ? "axis" : "axes"}, ${plural(variations, "variation")}, ${plural(scenarios, "scenario")}.`;
  }

  function copy() {
    if (!canCopy) return;
    if (props.kind === "workflow" && planQuery.data) {
      props.onCopy(planQuery.data, withPins);
    } else if (props.kind === "blueprint" && bpQuery.data) {
      // A clone: the form must never edit the query cache's object.
      props.onCopy({ spec: structuredClone(bpQuery.data.spec), seed: bpQuery.data.seed }, srcName);
    }
  }

  const title = props.kind === "workflow" ? "Copy workflow from plan" : "Copy blueprint from plan";
  return (
    <ModalShell label={title} onClose={onClose} closeOnBackdrop>
      <h3>{title}</h3>
      <p>
        {props.kind === "workflow"
          ? "Loads another plan's workflow into the editor. Nothing changes until you press Save."
          : "Replaces this plan's axes, variations, scenarios and sampling seed with another plan's. No rows are written until you Apply."}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
          Dataset
          <select className="input" aria-label="Source dataset" value={sourceDatasetId}
            onChange={(e) => { setSourceDatasetId(e.target.value); setSourcePlanId(""); }}>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>{d.name}{d.id === datasetId ? " (this dataset)" : ""}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 4 }}>
          Plan
          <select className="input" aria-label="Source plan" value={srcId} disabled={eligible.length === 0}
            onChange={(e) => setSourcePlanId(e.target.value)}>
            {eligible.length === 0 && <option value="">{plansLoading ? "Loading…" : "(no other plans)"}</option>}
            {eligible.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        {srcId && (
          <p role="status" data-testid="copy-from-plan-summary" style={{ fontSize: 12.5, margin: 0 }}>
            {loading ? "Loading…" : failed ? "Could not read this plan." : summary}
          </p>
        )}
        {props.kind === "workflow" && (
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
            <input type="checkbox" className="checkbox" checked={withPins} onChange={(e) => setWithPins(e.target.checked)} />
            Also copy its pinned parameters and output settings
          </label>
        )}
        {props.kind === "workflow" && !withPins && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            This plan keeps its own pins. Any pin whose node is not in the new workflow shows as "not in workflow".
          </p>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!canCopy} onClick={copy}>
          {props.kind === "workflow" ? "Load workflow" : props.replacing ? "Replace blueprint" : "Copy blueprint"}
        </button>
      </div>
    </ModalShell>
  );
}
