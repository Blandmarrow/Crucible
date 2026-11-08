import { useId, useState } from "react";
import { useMutation, useQueryClient, useIsMutating } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi, type DatasetProvenance } from "../../api/datasets";
import { EMPTY_PROVENANCE } from "../../constants/licenses";
import { apiErrorDetail } from "../../utils/apiError";
import type { Dataset } from "../../types";
import ModalShell from "../common/ModalShell";
import ProvenanceFields from "../common/ProvenanceFields";
import CategoryPicker from "./CategoryPicker";

// Shared by every mount, so a request still in flight after this dialog
// closed keeps the reopened one's submit disabled, as when the page owned
// the mutation.
const MUTATION_KEY = ["datasets-dialog", "create"] as const;

interface Props {
  existingCategories: string[];
  /** Runs once the dataset exists, as the dialog closes. */
  onCreated: (ds: Dataset) => void;
  onClose: () => void;
}

export default function NewDatasetDialog({ existingCategories, onCreated, onClose }: Props) {
  // Prefix for the dialog fields' `id`/`htmlFor` pairs (GPT-029).
  const uid = useId();
  const qc = useQueryClient();

  // The page mounts this only while open, so every open starts with a blank draft
  // and Escape / Cancel discard it by unmounting.
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newProvenance, setNewProvenance] = useState<DatasetProvenance>(EMPTY_PROVENANCE);

  const createMutation = useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: () => datasetsApi.create(newName, newDesc, newCategory, newProvenance),
    onSuccess: (ds) => {
      qc.invalidateQueries({ queryKey: ["datasets"] });
      onClose();
      onCreated(ds);
      toast.success(`Dataset "${ds.name}" created`);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Failed to create dataset")),
  });
  const pending = useIsMutating({ mutationKey: MUTATION_KEY }) > 0;

  return (
    <ModalShell label="New Dataset" onClose={onClose}>
      <h3>New Dataset</h3>
      <p>Give it a name and optional description.</p>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 18 }}>
        <div>
          <label className="label" htmlFor={`${uid}-new-name`}>Name</label>
          <input id={`${uid}-new-name`} className="input" placeholder="my_dataset" value={newName} autoFocus
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && newName && createMutation.mutate()} />
        </div>
        <div>
          <label className="label" htmlFor={`${uid}-new-desc`}>Description <span style={{ fontWeight: 400, color: "var(--fg-mute)", fontSize: 11 }}>(optional)</span></label>
          <input id={`${uid}-new-desc`} className="input" placeholder="…" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} />
        </div>
        <CategoryPicker
          value={newCategory}
          onChange={setNewCategory}
          existingCategories={existingCategories}
          label="Category"
          labelNote="(optional)"
          autoFocusNew={false}
        />
        <ProvenanceFields
          value={newProvenance}
          onChange={setNewProvenance}
          note="Defaults inherited by every image in this dataset unless the image sets its own."
        />
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={() => createMutation.mutate()} disabled={!newName || pending}>Create</button>
      </div>
    </ModalShell>
  );
}
