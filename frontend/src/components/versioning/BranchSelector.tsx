import { useState, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { versioningApi } from "../../api/versioning";
import { useJobSSE } from "../../hooks/useSSE";
import { useJobStore } from "../../store/jobStore";
import { BRANCH_SNAPSHOT_KEY } from "../../constants/storage";
import JobProgressBar from "../common/JobProgressBar";
import type { Branch } from "../../types";

interface Props {
  datasetId: string;
  branches: Branch[];
  activeBranchId: string | undefined;
  onSelect: (branchId: string) => void;
}

function readSnapshotPref(): "ask" | "auto" {
  return localStorage.getItem(BRANCH_SNAPSHOT_KEY) === "auto" ? "auto" : "ask";
}

function SnapshotPrompt({
  message,
  onYes,
  onNo,
}: {
  message: string;
  onYes: () => void;
  onNo: () => void;
}) {
  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 300,
    }}>
      <div className="panel" style={{ width: 380, padding: 0 }}>
        <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <p style={{ margin: 0, fontSize: 13 }}>{message}</p>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn ghost" onClick={onNo}>No, skip snapshot</button>
            <button className="btn primary" onClick={onYes}>Yes, create snapshot</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function BranchSelector({ datasetId, branches, activeBranchId, onSelect }: Props) {
  const qc = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [checkoutJobId, setCheckoutJobId] = useState<string | null>(null);
  const [pendingBranchId, setPendingBranchId] = useState<string | null>(null);

  const [checkoutPrompt, setCheckoutPrompt] = useState<string | null>(null);
  const [createPrompt, setCreatePrompt] = useState<string | null>(null);

  useJobSSE(checkoutJobId);
  const checkoutProgress = useJobStore((s) =>
    checkoutJobId ? s.activeJobs.get(checkoutJobId) : undefined
  );

  useEffect(() => {
    if (checkoutProgress?.status === "completed") {
      qc.invalidateQueries({ queryKey: ["branches", datasetId] });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      qc.invalidateQueries({ queryKey: ["image"] });
      qc.invalidateQueries({ queryKey: ["caption"] });
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

  async function doCheckout(branchId: string, preRestoreSnapshot: boolean) {
    setPendingBranchId(branchId);
    try {
      const result = await versioningApi.checkoutBranch(datasetId, branchId, preRestoreSnapshot);
      setCheckoutJobId(result.job_id);
    } catch {
      toast.error("Checkout failed");
      setPendingBranchId(null);
    }
  }

  function handleSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const val = e.target.value;
    if (val === "__new__") {
      setShowNew(true);
      return;
    }
    if (val === (activeBranchId ?? branches[0]?.id)) return;
    if (readSnapshotPref() === "ask") {
      setCheckoutPrompt(val);
    } else {
      doCheckout(val, true);
    }
  }

  async function doCreateBranch(name: string, includeSnapshot: boolean) {
    try {
      const result = await versioningApi.createBranch(datasetId, name, undefined, includeSnapshot);
      if ("job_id" in result) {
        toast.success("Creating branch…");
      } else {
        qc.invalidateQueries({ queryKey: ["branches", datasetId] });
        onSelect(result.id);
        toast.success(`Branch '${name}' created`);
      }
      setShowNew(false);
      setNewName("");
    } catch {
      toast.error("Failed to create branch");
    }
  }

  function handleCreateBranch(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed) return;
    if (readSnapshotPref() === "ask") {
      setCreatePrompt(trimmed);
    } else {
      doCreateBranch(trimmed, true);
    }
  }

  const effectiveBranchId = activeBranchId ?? branches[0]?.id ?? "";

  return (
    <>
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
          <span style={{ fontSize: 11, color: "var(--fg-mute)" }}>
            {checkoutProgress?.percent != null ? `${Math.round(checkoutProgress.percent)}%` : "Switching…"}
          </span>
        )}
      </div>

      {checkoutJobId && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 250,
          width: 280, background: "var(--surface-2)", border: "1px solid var(--line)",
          borderRadius: "var(--r)", padding: "12px 14px", boxShadow: "0 4px 16px rgba(0,0,0,0.3)",
        }}>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 8, color: "var(--fg)" }}>
            Switching branch…
          </div>
          <JobProgressBar
            message={checkoutProgress?.message ?? checkoutProgress?.current_item ?? "Restoring files…"}
            percent={checkoutProgress?.percent ?? 0}
          />
        </div>
      )}

      {checkoutPrompt && (
        <SnapshotPrompt
          message="Save the current state as a snapshot before switching branches?"
          onYes={() => { setCheckoutPrompt(null); doCheckout(checkoutPrompt, true); }}
          onNo={() => { setCheckoutPrompt(null); doCheckout(checkoutPrompt, false); }}
        />
      )}

      {createPrompt && (
        <SnapshotPrompt
          message={`Create an initial snapshot for branch "${createPrompt}"?`}
          onYes={() => { const n = createPrompt; setCreatePrompt(null); doCreateBranch(n, true); }}
          onNo={() => { const n = createPrompt; setCreatePrompt(null); doCreateBranch(n, false); }}
        />
      )}
    </>
  );
}
