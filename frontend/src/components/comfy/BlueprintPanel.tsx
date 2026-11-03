import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../../utils/apiError";
import ConfirmDialog from "../common/ConfirmDialog";
import OutlineModal from "./OutlineModal";
import {
  blueprintPath, comfyApi, EMPTY_BLUEPRINT_SPEC,
  type BlueprintSpec, type BlueprintValue,
} from "../../api/comfy";
import { useDebouncedPersist } from "../../hooks/useDebouncedPersist";
import { serverWritesSettled, writeToServer } from "../../utils/serverWriter";
import ServerStateGate from "../common/ServerStateGate";
import BlueprintAxesEditor from "./BlueprintAxesEditor";
import BlueprintVariationsEditor from "./BlueprintVariationsEditor";
import BlueprintScenariosEditor from "./BlueprintScenariosEditor";
import BlueprintPreview from "./BlueprintPreview";
import { renameRefs } from "./blueprintKeys";

interface Props {
  planId: string;
  datasetId: string;
  hasPromptPin: boolean;
  /** A run is live on this plan: the server refuses Apply, so the form is read-only. */
  running: boolean;
  /** The run bar's Base folder and Prompt-as-caption toggle, which Top up runs with. */
  subfolder: string;
  setCaption: boolean;
  onRunStarted: (jobId: string, planId: string) => void;
}

const MAX_SEED = 2 ** 31 - 1;
const randomSeed = () => Math.floor(Math.random() * MAX_SEED);

export default function BlueprintPanel(props: Props) {
  const { planId } = props;
  return (
    <ServerStateGate
      queryKey={["comfy", "blueprint", planId]}
      load={async () => {
        await serverWritesSettled(blueprintPath(planId));
        return comfyApi.getBlueprint(planId);
      }}
      label="this plan's blueprint"
    >
      {(saved) => (
        <BlueprintForm
          {...props}
          initial={{ spec: saved?.spec ?? EMPTY_BLUEPRINT_SPEC, seed: saved?.seed ?? randomSeed() }}
        />
      )}
    </ServerStateGate>
  );
}

function BlueprintForm({ planId, datasetId, hasPromptPin, running, subfolder, setCaption, onRunStarted, initial }: Props & { initial: BlueprintValue }) {
  const [spec, setSpec] = useState<BlueprintSpec>(initial.spec);
  const [seed, setSeed] = useState(initial.seed);
  const value: BlueprintValue = { spec, seed };
  const [outlining, setOutlining] = useState(false);
  const [confirmUse, setConfirmUse] = useState(false);
  const [busy, setBusy] = useState(false);

  // The draft comes from its own query: the gate above latches its first value and never
  // refetches into the form, so it cannot carry a draft that lands while the tab is open.
  const draftQuery = useQuery({
    queryKey: ["comfy", "blueprint-draft", planId],
    queryFn: () => comfyApi.getBlueprint(planId),
    staleTime: 0,
  });
  const draft = draftQuery.data?.draft ?? null;
  const specIsEmpty = Object.keys(spec.axes).length === 0 && Object.keys(spec.variations).length === 0
    && spec.scenarios.length === 0;

  async function discardDraft() {
    setBusy(true);
    try {
      await comfyApi.discardBlueprintDraft(planId);
      await draftQuery.refetch();
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not discard the draft"));
    } finally {
      setBusy(false);
    }
  }

  async function applyDraft() {
    if (!draft) return;
    setConfirmUse(false);
    setBusy(true);
    try {
      // The draft is cleared only after the spec holding it is on the server. Not via
      // writeToServer: it drops a 4xx and its settle then resolves, which would clear a
      // draft whose save never landed. Older autosaves land first, so none can overwrite
      // this save; the form takes the draft only once it is saved.
      await serverWritesSettled(blueprintPath(planId));
      await comfyApi.putBlueprint(planId, { spec: draft, seed });
      setSpec(draft);
      await comfyApi.discardBlueprintDraft(planId);
      await draftQuery.refetch();
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not use the draft"));
    } finally {
      setBusy(false);
    }
  }

  // A plan with no blueprint starts from a random seed that is only saved with the first edit.
  useDebouncedPersist(planId, value, undefined, (_key, v) => writeToServer(blueprintPath(planId), "PUT", v));

  return (
    <div data-testid="blueprint-panel">
      <div style={{ margin: "0 0 12px" }}>
        <button className="btn sm" disabled={running} data-testid="outline-open" onClick={() => setOutlining(true)}
          title={running ? "A run is in progress on this plan" : "Have an LLM draft a blueprint from a description"}>
          ✨ Draft from a description…
        </button>
      </div>
      {draft && (
        <div role="status" data-testid="blueprint-draft-banner"
          style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12, margin: "0 0 12px",
            padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 6 }}>
          <span style={{ flex: 1 }}>
            A drafted blueprint is ready: {Object.keys(draft.axes).length} axes, {draft.scenarios.length} scenarios
          </span>
          <button className="btn primary sm" disabled={running || busy}
            onClick={() => (specIsEmpty ? applyDraft() : setConfirmUse(true))}>Use it</button>
          <button className="btn ghost sm" disabled={busy} onClick={discardDraft}>Discard</button>
        </div>
      )}
      {confirmUse && (
        <ConfirmDialog title="Use the draft" message="Replace the current blueprint with the draft?"
          confirmLabel="Replace" onConfirm={applyDraft} onCancel={() => setConfirmUse(false)} />
      )}
      {outlining && (
        <OutlineModal planId={planId} initialBrief={draftQuery.data?.brief ?? null} onClose={() => setOutlining(false)} />
      )}
      <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 12px" }}>
        Describe the images you want as axes, variations and scenarios. The preview shows the folders and
        prompt rows this makes. Apply adds only the rows that are missing. Your changes save as you type.
      </p>
      {running && (
        <p role="status" style={{ fontSize: 12, color: "var(--warn, var(--fg-mute))", margin: "0 0 12px" }}>
          A run is in progress on this plan, so the blueprint is read-only until it finishes.
        </p>
      )}
      <fieldset disabled={running} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <BlueprintAxesEditor axes={spec.axes} onChange={(axes) => setSpec({ ...spec, axes })}
          onRename={(from, to) => setSpec(renameRefs(spec, "axes", from, to))} />
        <BlueprintVariationsEditor variations={spec.variations} onChange={(variations) => setSpec({ ...spec, variations })}
          onRename={(from, to) => setSpec(renameRefs(spec, "variations", from, to))} />
        <BlueprintScenariosEditor spec={spec} onChange={(scenarios) => setSpec({ ...spec, scenarios })} />
        <div style={{ margin: "0 0 12px" }}>
          <button className="btn ghost sm" onClick={() => setSeed(randomSeed())}
            title="Sample scenarios pick different folders and variation pieces with a new seed">
            New sampling seed
          </button>
        </div>
        <BlueprintPreview
          planId={planId} datasetId={datasetId} value={value}
          hasPromptPin={hasPromptPin && !running}
          subfolder={subfolder} setCaption={setCaption} onRunStarted={onRunStarted}
        />
      </fieldset>
    </div>
  );
}
