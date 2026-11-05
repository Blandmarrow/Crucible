import { useLayoutEffect, useRef, useState } from "react";
import { COMFY_BLUEPRINT_SECTIONS_KEY } from "../../constants/storage";
import { loadPersisted, savePersisted } from "../../utils/persistentState";
import type { SectionControl } from "./BlueprintSection";
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

type SectionKey = "axes" | "variations" | "scenarios";
type OpenSections = Record<SectionKey, boolean>;
// Open by default: a fresh plan shows every editor (and the e2e expects that).
const ALL_OPEN: OpenSections = { axes: true, variations: true, scenarios: true };
const ALL_CLOSED: OpenSections = { axes: false, variations: false, scenarios: false };
const SECTIONS: { key: SectionKey; title: string }[] = [
  { key: "axes", title: "Axes" }, { key: "variations", title: "Variations" }, { key: "scenarios", title: "Scenarios" },
];

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

  /** Resolves true once the draft is the saved spec (the review modal closes on it). */
  async function applyDraft(): Promise<boolean> {
    if (!draft) return false;
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
      return true;
    } catch (err) {
      toast.error(apiErrorDetail(err, "Could not use the draft"));
      return false;
    } finally {
      setBusy(false);
    }
  }

  // A plan with no blueprint starts from a random seed that is only saved with the first edit.
  useDebouncedPersist(planId, value, undefined, (_key, v) => writeToServer(blueprintPath(planId), "PUT", v));

  // Which editor sections are open, per plan, in this browser. The form is keyed by the
  // plan, so the initializer runs once per plan.
  const sectionsKey = `${COMFY_BLUEPRINT_SECTIONS_KEY}-${planId}`;
  const [openSections, setOpenSections] = useState<OpenSections>(() => loadPersisted(sectionsKey, ALL_OPEN));
  const writeSections = (next: OpenSections) => {
    setOpenSections(next);
    savePersisted(sectionsKey, next);
  };
  // The jump bar wraps in a narrow pane, so its height is measured, not assumed: index.css
  // reads --bp-jump-h to keep a jump target or a newly focused field out from under it.
  const wrapRef = useRef<HTMLDivElement>(null);
  const jumpBarRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const bar = jumpBarRef.current;
    if (!wrap || !bar) return;
    const update = () => wrap.style.setProperty("--bp-jump-h", `${bar.offsetHeight}px`);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(bar);
    return () => ro.disconnect();
  }, []);
  const axesRef = useRef<HTMLDivElement>(null);
  const variationsRef = useRef<HTMLDivElement>(null);
  const scenariosRef = useRef<HTMLDivElement>(null);
  const control = (k: SectionKey): SectionControl => ({
    open: openSections[k],
    setOpen: (open) => writeSections({ ...openSections, [k]: open }),
    readOnly: running,
  });
  function jumpTo(k: SectionKey) {
    if (!openSections[k]) writeSections({ ...openSections, [k]: true });
    const el = { axes: axesRef, variations: variationsRef, scenarios: scenariosRef }[k].current;
    // After the render that opens it, so the scroll lands on its full height.
    requestAnimationFrame(() => el?.scrollIntoView({ block: "start", behavior: "smooth" }));
  }
  const counts: Record<SectionKey, number> = {
    axes: Object.keys(spec.axes).length,
    variations: Object.keys(spec.variations).length,
    scenarios: spec.scenarios.length,
  };

  return (
    <div data-testid="blueprint-panel" className="bp-wrap" ref={wrapRef}>
     <div className="bp-grid">
      <div style={{ minWidth: 0 }}>
      {draft && (
        // Accent-coloured on purpose: the draft waits here, unused, until the user acts on
        // it, and a quiet grey box was easy to miss after closing the modal.
        <div role="status" data-testid="blueprint-draft-banner"
          style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13, margin: "0 0 12px",
            padding: "10px 12px", border: "1px solid var(--accent)", borderRadius: 6, background: "var(--accent-glow)" }}>
          <span style={{ flex: 1 }}>
            ✨ <strong>A drafted blueprint is waiting for you:</strong>{" "}
            {Object.keys(draft.axes).length} axes, {draft.scenarios.length} scenarios. Nothing changes until you use it.
          </span>
          <button className="btn sm" disabled={busy} data-testid="blueprint-draft-review"
            onClick={() => setOutlining(true)}>Review…</button>
          <button className="btn primary sm" disabled={running || busy}
            onClick={() => (specIsEmpty ? void applyDraft() : setConfirmUse(true))}>Use it</button>
          <button className="btn ghost sm" disabled={busy} onClick={discardDraft}>Discard</button>
        </div>
      )}
      {confirmUse && (
        <ConfirmDialog title="Use the draft" message="Replace the current blueprint with the draft?"
          confirmLabel="Replace" onConfirm={() => void applyDraft()} onCancel={() => setConfirmUse(false)} />
      )}
      {outlining && (
        <OutlineModal planId={planId} initialBrief={draftQuery.data?.brief ?? null}
          draft={draft} seed={seed} specIsEmpty={specIsEmpty} running={running} busy={busy}
          onUse={applyDraft} onDiscard={discardDraft} onClose={() => setOutlining(false)} />
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
      {/* Sticky under the page header. Buttons, not #hash links: no router hash, and no
          duplicate ids when two panes show this page. */}
      <div className="bp-jump" data-testid="blueprint-jump" ref={jumpBarRef}>
        {SECTIONS.map(({ key, title }) => (
          <button key={key} type="button" className="btn ghost sm" onClick={() => jumpTo(key)}>
            {title} · {counts[key]}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button type="button" className="btn ghost sm" onClick={() => writeSections(ALL_CLOSED)}>Collapse all</button>
        <button type="button" className="btn ghost sm" onClick={() => writeSections(ALL_OPEN)}>Expand all</button>
        <button className="btn sm" disabled={running} data-testid="outline-open" onClick={() => setOutlining(true)}
          title={running ? "A run is in progress on this plan" : "Have an LLM draft a blueprint from a description"}>
          ✨ Draft from a description…
        </button>
      </div>
      {/* Each section carries its own read-only fieldset, so it still collapses mid-run. */}
      <BlueprintAxesEditor axes={spec.axes} onChange={(axes) => setSpec({ ...spec, axes })}
        onRename={(from, to) => setSpec(renameRefs(spec, "axes", from, to))}
        section={control("axes")} ref={axesRef} />
      <BlueprintVariationsEditor variations={spec.variations} onChange={(variations) => setSpec({ ...spec, variations })}
        onRename={(from, to) => setSpec(renameRefs(spec, "variations", from, to))}
        section={control("variations")} ref={variationsRef} />
      <BlueprintScenariosEditor spec={spec} onChange={(scenarios) => setSpec({ ...spec, scenarios })}
        section={control("scenarios")} ref={scenariosRef} />
      <div style={{ margin: "0 0 12px" }}>
        <button className="btn ghost sm" disabled={running} onClick={() => setSeed(randomSeed())}
          title="Sample scenarios pick different folders and variation pieces with a new seed">
          New sampling seed
        </button>
      </div>
      </div>
      {/* Outside the editors: its own `running` guard replaces the fieldset it sat in. */}
      <BlueprintPreview
        planId={planId} datasetId={datasetId} value={value}
        hasPromptPin={hasPromptPin} running={running}
        subfolder={subfolder} setCaption={setCaption} onRunStarted={onRunStarted}
      />
     </div>
    </div>
  );
}
