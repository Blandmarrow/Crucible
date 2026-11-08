import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { appDataApi } from "../../api/appData";
import { DECLARED_CATEGORIES_QUERY_KEY } from "../../constants/queryKeys";
import ModalShell from "../common/ModalShell";

interface Props {
  /** Every category name already in use (declared-empty and on a dataset). */
  existingNames: string[];
  onClose: () => void;
}

export default function NewCategoryDialog({ existingNames, onClose }: Props) {
  // Prefix for the dialog fields' `id`/`htmlFor` pairs (GPT-029).
  const uid = useId();
  const qc = useQueryClient();
  const [newCategoryName, setNewCategoryName] = useState("");

  const createCategoryMutation = useMutation({
    mutationFn: appDataApi.createCategory,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: DECLARED_CATEGORIES_QUERY_KEY });
      onClose();
    },
    onError: () => toast.error("Failed to create category"),
  });

  function handleCreateCategory() {
    const name = newCategoryName.trim();
    if (!name) return;
    if (existingNames.includes(name)) {
      toast("Category already exists");
      return;
    }
    createCategoryMutation.mutate(name);
  }

  return (
    // The one DatasetsPage dialog a backdrop click always closed, so it keeps that.
    <ModalShell label="New Category" onClose={onClose} closeOnBackdrop>
      <h3>New Category</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 18 }}>
        <label className="label" htmlFor={`${uid}-category-name`}>Category name</label>
        <input
          id={`${uid}-category-name`}
          className="input"
          autoFocus
          placeholder="e.g. Portraits"
          value={newCategoryName}
          onChange={(e) => setNewCategoryName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleCreateCategory(); }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn primary"
          disabled={!newCategoryName.trim()}
          onClick={handleCreateCategory}
        >
          Create
        </button>
      </div>
    </ModalShell>
  );
}
