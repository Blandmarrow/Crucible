import { useId, useState } from "react";
import { useMutation, useQueryClient, useIsMutating } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi, type DatasetProvenance } from "../../api/datasets";
import { useCustomLicenses } from "../../hooks/useCustomLicenses";
import { invalidateProvenanceScope } from "../../constants/queryKeys";
import { apiErrorDetail } from "../../utils/apiError";
import type { Dataset } from "../../types";
import ModalShell from "../common/ModalShell";
import ProvenanceFields from "../common/ProvenanceFields";
import CategoryPicker from "./CategoryPicker";
import GoalFields from "./GoalFields";
import { goalDraftOf, parseTarget, type GoalDraft } from "./goalDraft";

// Shared by every mount, so a request still in flight after this dialog
// closed keeps the reopened one's submit disabled, as when the page owned
// the mutation.
const MUTATION_KEY = ["datasets-dialog", "edit"] as const;

interface Props {
  dataset: Dataset;
  existingCategories: string[];
  onClose: () => void;
}

export default function EditDatasetDialog({ dataset, existingCategories, onClose }: Props) {
  // Prefix for the dialog fields' `id`/`htmlFor` pairs (GPT-029).
  const uid = useId();
  const qc = useQueryClient();

  // The page mounts this only while editing, so each open starts from the dataset.
  const [renameName, setRenameName] = useState(dataset.name);
  const [renameDesc, setRenameDesc] = useState(dataset.description ?? "");
  const [renameCategory, setRenameCategory] = useState(dataset.category ?? "");
  const [renameProvenance, setRenameProvenance] = useState<DatasetProvenance>({
    source_name: dataset.source_name ?? "",
    source_url: dataset.source_url ?? "",
    license: dataset.license ?? "",
    attribution: dataset.attribution ?? "",
  });
  const [renameGoal, setRenameGoal] = useState<GoalDraft>(goalDraftOf(dataset.goal, dataset.target_resolution));
  const target = parseTarget(renameGoal.target);

  // Free-text licenses already recorded in the dataset being edited — offered as
  // options for its default.
  const renameCustomLicenses = useCustomLicenses(dataset.id);

  // ── Rename modal: changed detection ───────────────────────────────────────
  const renameChanged =
    renameName !== dataset.name ||
    renameDesc !== (dataset.description ?? "") ||
    renameCategory !== (dataset.category ?? "") ||
    renameProvenance.source_name !== (dataset.source_name ?? "") ||
    renameProvenance.source_url !== (dataset.source_url ?? "") ||
    renameProvenance.license !== (dataset.license ?? "") ||
    renameProvenance.attribution !== (dataset.attribution ?? "") ||
    renameGoal.goal !== (dataset.goal ?? "") ||
    target !== (dataset.target_resolution ?? null);
  const canSave = !!renameName && renameChanged && target !== undefined;

  const renameMutation = useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: () =>
      datasetsApi.update(dataset.id, {
        name: renameName,
        description: renameDesc,
        category: renameCategory,
        // Editing these retroactively changes every image that hasn't
        // overridden the field — the intended inheritance behaviour.
        ...renameProvenance,
        goal: renameGoal.goal,
        target_resolution: target ?? null,
      }),
    onSuccess: (ds) => {
      // Not just ["datasets"]: the provenance defaults edited here are the
      // effective source/license of every image that has not overridden them, so
      // gallery badges, Stats and any open Export preview are all stale now.
      invalidateProvenanceScope(qc);
      // The goal and target change which findings apply.
      qc.invalidateQueries({ queryKey: ["dataset-health", dataset.id] });
      onClose();
      toast.success(`Updated "${ds.name}"`);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to update dataset")),
  });
  const pending = useIsMutating({ mutationKey: MUTATION_KEY }) > 0;

  return (
    <ModalShell label="Edit Dataset" onClose={onClose}>
      <h3>Edit Dataset</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
        <div>
          <label className="label" htmlFor={`${uid}-edit-name`}>Name</label>
          <input
            id={`${uid}-edit-name`}
            className="input"
            value={renameName}
            autoFocus
            onChange={(e) => setRenameName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && canSave) renameMutation.mutate();
            }}
          />
        </div>
        <div>
          <label className="label" htmlFor={`${uid}-edit-desc`}>Description <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
          <input
            id={`${uid}-edit-desc`}
            className="input"
            placeholder="…"
            value={renameDesc}
            onChange={(e) => setRenameDesc(e.target.value)}
          />
        </div>
        <CategoryPicker
          value={renameCategory}
          onChange={setRenameCategory}
          existingCategories={existingCategories}
          label="Category"
          labelNote="(optional — groups datasets into folders)"
          autoFocusNew={false}
        />
        <GoalFields value={renameGoal} onChange={setRenameGoal} />
        <ProvenanceFields
          value={renameProvenance}
          onChange={setRenameProvenance}
          note="Defaults for every image in this dataset that hasn't set its own. Changing them updates all non-overridden images."
          customLicenses={renameCustomLicenses}
        />
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button
          className="btn primary"
          onClick={() => renameMutation.mutate()}
          disabled={!canSave || pending}
        >
          Save
        </button>
      </div>
    </ModalShell>
  );
}
