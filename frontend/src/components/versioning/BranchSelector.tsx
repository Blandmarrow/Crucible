import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { versioningApi } from "../../api/versioning";
import { useJobSSE } from "../../hooks/useSSE";
import { useJobStore } from "../../store/jobStore";
import type { Branch } from "../../types";

interface Props {
  datasetId: string;
  branches: Branch[];
  activeBranchId: string | undefined;
  onSelect: (branchId: string) => void;
}

export default function BranchSelector({ datasetId, branches, activeBranchId, onSelect }: Props) {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [checkoutJobId, setCheckoutJobId] = useState<string | null>(null);
  const [pendingBranchId, setPendingBranchId] = useState<string | null>(null);

  useJobSSE(checkoutJobId);
  const checkoutProgress = useJobStore((s) =>
    checkoutJobId ? s.activeJobs.get(checkoutJobId) : undefined
  );

  useEffect(() => {
    if (checkoutProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["branches", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      toast.success("Switched branch");
      if (pendingBranchId) onSelect(pendingBranchId);
      setPendingBranchId(null);
      setCheckoutJobId(null);
    } else if (checkoutProgress?.status === "failed") {
      toast.error("Branch checkout failed");
      setPendingBranchId(null);
      setCheckoutJobId(null);
    }
  }, [checkoutProgress?.status, qc, datasetId, onSelect, pendingBranchId]);

  async function handleSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const val = e.target.value;
    if (val === "__new__") {
      setShowNew(true);
      return;
    }
    if (val === activeBranchId) return;
    setPendingBranchId(val);
    try {
      const result = await versioningApi.checkoutBranch(datasetId, val);
      setCheckoutJobId(result.job_id);
    } catch {
      toast.error("Checkout failed");
      setPendingBranchId(null);
    }
  }

  async function handleCreateBranch(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed) return;
    try {
      const result = await versioningApi.createBranch(datasetId, trimmed);
      if ("job_id" in result) {
        toast.success("Creating branch…");
      } else {
        qc.invalidateQueries({ queryKey: ["branches", datasetId] });
        onSelect(result.id);
        toast.success(`Branch '${trimmed}' created`);
      }
      setShowNew(false);
      setNewName("");
    } catch {
      toast.error("Failed to create branch");
    }
  }

  const effectiveBranchId = activeBranchId ?? branches[0]?.id ?? "";

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {showNew ? (
        <form onSubmit={handleCreateBranch} style={{ display: "flex", gap: 6 }}>
          <input
            className="input"
            style={{ height: 28, fontSize: 12, padding: "0 8px" }}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Branch name"
            autoFocus
          />
          <button type="submit" className="btn sm primary" disabled={!newName.trim()}>Create</button>
          <button type="button" className="btn sm ghost" onClick={() => setShowNew(false)}>Cancel</button>
        </form>
      ) : (
        <select
          className="select"
          style={{ height: 28, fontSize: 12, paddingLeft: 8 }}
          value={effectiveBranchId}
          onChange={handleSelect}
          disabled={!!checkoutJobId}
        >
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}{b.id === effectiveBranchId ? " ●" : ""}
            </option>
          ))}
          <option value="__new__">+ New branch…</option>
        </select>
      )}
      {checkoutJobId && (
        <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>Switching…</span>
      )}
    </div>
  );
}
