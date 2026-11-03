import { useState } from "react";
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

function BlueprintForm({ planId, datasetId, hasPromptPin, running, initial }: Props & { initial: BlueprintValue }) {
  const [spec, setSpec] = useState<BlueprintSpec>(initial.spec);
  const [seed, setSeed] = useState(initial.seed);
  const value: BlueprintValue = { spec, seed };

  // A plan with no blueprint starts from a random seed that is only saved with the first edit.
  useDebouncedPersist(planId, value, undefined, (_key, v) => writeToServer(blueprintPath(planId), "PUT", v));

  return (
    <div data-testid="blueprint-panel">
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
        />
      </fieldset>
    </div>
  );
}
