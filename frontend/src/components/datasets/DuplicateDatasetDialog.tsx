import { useId, useState } from "react";
import { useMutation, useQuery, useIsMutating } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { datasetsApi } from "../../api/datasets";
import { versioningApi } from "../../api/versioning";
import type { Dataset } from "../../types";
import ModalShell from "../common/ModalShell";
import { formatDate, formatSize } from "./format";

// Shared by every mount, so a request still in flight after this dialog
// closed keeps the reopened one's submit disabled, as when the page owned
// the mutation.
const MUTATION_KEY = ["datasets-dialog", "duplicate"] as const;

interface Props {
  dataset: Dataset;
  versioningEnabled: boolean;
  /** Hands the job to the page, whose watcher outlives this dialog. */
  onStarted: (jobId: string) => void;
  onClose: () => void;
}

export default function DuplicateDatasetDialog({ dataset, versioningEnabled, onStarted, onClose }: Props) {
  // Prefix for the dialog fields' `id`/`htmlFor` pairs (GPT-029).
  const uid = useId();
  // The page mounts this only while open, so each open starts from these.
  const [duplicateName, setDuplicateName] = useState(`${dataset.name} (copy)`);
  const [duplicateVersionId, setDuplicateVersionId] = useState<string | undefined>(undefined);
  const [dupBranchId, setDupBranchId] = useState<string | undefined>(undefined);
  // Off by default, matching the backend: doubling the footage is a costed
  // choice, not something a duplicate does quietly.
  const [duplicateIncludeVideos, setDuplicateIncludeVideos] = useState(false);

  const { data: dupBranches = [] } = useQuery({
    queryKey: ["branches", dataset.id],
    queryFn: () => versioningApi.listBranches(dataset.id),
    enabled: versioningEnabled,
  });

  const resolvedDupBranchId = dupBranchId ?? dataset.current_branch_id ?? dupBranches[0]?.id;

  const { data: dupVersions = [] } = useQuery({
    queryKey: ["versions", dataset.id, resolvedDupBranchId, "dup"],
    queryFn: () => versioningApi.listVersions(dataset.id, { branchId: resolvedDupBranchId }),
    enabled: versioningEnabled && !!resolvedDupBranchId,
  });

  const duplicateMutation = useMutation({
    mutationKey: MUTATION_KEY,
    mutationFn: () =>
      datasetsApi.duplicate(
        dataset.id, duplicateName, duplicateVersionId,
        // Never send true alongside a snapshot: the backend 400s that pairing,
        // and the checkbox is disabled rather than cleared, so the state can
        // still read true while a version is picked.
        duplicateVersionId ? false : duplicateIncludeVideos,
      ),
    onSuccess: (data) => {
      onClose();
      onStarted(data.job_id);
      toast.success("Duplicating dataset…");
    },
    onError: () => toast.error("Failed to duplicate dataset"),
  });
  const pending = useIsMutating({ mutationKey: MUTATION_KEY }) > 0;

  return (
    <ModalShell label="Duplicate Dataset" onClose={onClose}>
      <h3>Duplicate Dataset</h3>
      <p style={{ marginBottom: 16 }}>
        Source: <strong style={{ color: "var(--fg)" }}>{dataset.name}</strong>
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 18 }}>
        <div>
          <label className="label" htmlFor={`${uid}-dup-name`}>New name</label>
          <input
            id={`${uid}-dup-name`}
            className="input"
            autoFocus
            value={duplicateName}
            onChange={(e) => setDuplicateName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && duplicateName && !pending)
                duplicateMutation.mutate();
            }}
          />
        </div>

        {/* Snapshot source — only shown when versioning is enabled */}
        {versioningEnabled && (
          <div>
            <label className="label" style={{ marginBottom: 8 }}>Source snapshot</label>
            {dupBranches.length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
                No snapshots yet — will duplicate current state
              </p>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "var(--fg-mute)", minWidth: 50 }}>Branch</span>
                  <select
                    className="select"
                    style={{ flex: 1 }}
                    value={resolvedDupBranchId ?? ""}
                    onChange={(e) => {
                      setDupBranchId(e.target.value || undefined);
                      setDuplicateVersionId(undefined);
                    }}
                  >
                    {dupBranches.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "var(--fg-mute)", minWidth: 50 }}>Version</span>
                  <select
                    className="select"
                    style={{ flex: 1 }}
                    value={duplicateVersionId ?? ""}
                    onChange={(e) => setDuplicateVersionId(e.target.value || undefined)}
                  >
                    <option value="">Current state</option>
                    {dupVersions.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name ?? "Snapshot"} — {formatDate(v.created_at)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Videos are counted apart from images and are not copied by
            default — so the cost is spelled out from the columns the card
            already shows, and the row only exists when there is footage
            to carry. */}
        {dataset.video_count > 0 && (
          <div>
            <label
              className="label"
              style={{
                display: "flex", alignItems: "center", gap: 8, marginBottom: 0,
                cursor: duplicateVersionId ? "not-allowed" : "pointer",
                opacity: duplicateVersionId ? 0.5 : 1,
              }}
            >
              <input
                type="checkbox"
                checked={!duplicateVersionId && duplicateIncludeVideos}
                disabled={!!duplicateVersionId}
                onChange={(e) => setDuplicateIncludeVideos(e.target.checked)}
              />
              Copy {dataset.video_count}{" "}
              {dataset.video_count === 1 ? "video" : "videos"}{" "}
              ({formatSize(dataset.video_size_bytes)})
            </label>
            {duplicateVersionId && (
              <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "6px 0 0 24px" }}>
                Snapshots don't capture videos
              </p>
            )}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          className="btn ghost"
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          className="btn primary"
          onClick={() => duplicateMutation.mutate()}
          disabled={!duplicateName || pending}
        >
          Duplicate
        </button>
      </div>
    </ModalShell>
  );
}
