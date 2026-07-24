import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { apiErrorDetail } from "../utils/apiError";
import { Pin } from "lucide-react";
import { settingsApi } from "../api/settings";
import { versioningApi } from "../api/versioning";
import { datasetsApi } from "../api/datasets";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { VERSIONS_BRANCH_KEY } from "../constants/storage";
import type { Version } from "../types";
import CreateSnapshotModal from "../components/versioning/CreateSnapshotModal";
import DiffModal from "../components/versioning/DiffModal";
import RestoreConfirmModal from "../components/versioning/RestoreConfirmModal";
import BranchSelector from "../components/versioning/BranchSelector";
import ConfirmDialog from "../components/common/ConfirmDialog";

const SOURCE_BADGE: Record<string, { label: string; cls: string }> = {
  manual:      { label: "Manual",      cls: "badge solid" },
  pre_restore: { label: "Pre-restore", cls: "badge warn" },
  branch_init: { label: "Branch init", cls: "badge info" },
};

function SourceBadge({ source }: { source: string }) {
  const cfg = SOURCE_BADGE[source] ?? { label: source, cls: "badge solid" };
  return <span className={cfg.cls} style={{ fontSize: 10 }}>{cfg.label}</span>;
}

function VersionCard({
  version, onRestore, onDelete, onTogglePin, isHead,
}: {
  version: Version;
  onRestore: (v: Version) => void;
  onDelete: (v: Version) => void;
  onTogglePin: (v: Version, pinned: boolean) => void;
  isHead: boolean;
}) {
  const date = new Date(version.created_at);
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 16px",
      borderBottom: "1px solid var(--line)",
      background: version.is_pinned ? "rgba(16,185,129,.04)" : undefined,
    }}>
      <div style={{
        width: 10, height: 10, borderRadius: "50%", marginTop: 4, flexShrink: 0,
        background: isHead ? "var(--accent)" : "var(--surface-3)",
        border: "2px solid var(--line)",
      }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
          <span style={{ fontWeight: 500, fontSize: 13 }}>
            {version.name ?? `Snapshot ${version.id.slice(0, 8)}`}
          </span>
          {isHead && <span className="badge solid" style={{ fontSize: 10 }}>Current</span>}
          <SourceBadge source={version.source} />
        </div>
        {version.description && (
          <div style={{ fontSize: 12, color: "var(--fg-mute)", marginBottom: 2 }}>
            {version.description}
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--fg-dim)" }}>
          {version.image_count} images · {date.toLocaleString()}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center" }}>
        <button
          className="icon-btn"
          title={version.is_pinned ? "Unpin" : "Pin"}
          style={{ color: version.is_pinned ? "var(--accent)" : undefined }}
          onClick={() => onTogglePin(version, !version.is_pinned)}
        >
          <Pin size={14} />
        </button>
        <button className="btn sm ghost" onClick={() => onRestore(version)}>Restore</button>
        <button className="btn sm ghost" style={{ color: "var(--bad)" }} onClick={() => onDelete(version)}>
          Delete
        </button>
      </div>
    </div>
  );
}

export default function VersionsPage() {
  const datasetId = usePaneDatasetId();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDiffModal, setShowDiffModal] = useState(false);
  const [showPruneConfirm, setShowPruneConfirm] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<Version | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Version | null>(null);
  const [activeBranchId, setActiveBranchId] = useState<string | undefined>(
    () => sessionStorage.getItem(`${VERSIONS_BRANCH_KEY}-${datasetId}`) ?? undefined
  );

  function handleBranchSelect(branchId: string) {
    sessionStorage.setItem(`${VERSIONS_BRANCH_KEY}-${datasetId}`, branchId);
    setActiveBranchId(branchId);
  }

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [createdAfter, setCreatedAfter] = useState("");
  const [createdBefore, setCreatedBefore] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data: settings } = useQuery({
    queryKey: ["settings", "thresholds"],
    queryFn: settingsApi.getThresholds,
    staleTime: 60_000,
  });

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
    staleTime: 30_000,
  });

  const { data: branches = [] } = useQuery({
    queryKey: ["branches", datasetId],
    queryFn: () => versioningApi.listBranches(datasetId!),
    enabled: !!datasetId && settings?.versioning_mode !== "off",
  });

  // Sync activeBranchId when current_branch_id changes externally (e.g. sidebar branch switch).
  // Only fires on changes after the initial data load — sessionStorage preference is preserved on mount.
  const prevCurrentBranchId = useRef<string | undefined>(undefined);
  useEffect(() => {
    const curr = dataset?.current_branch_id;
    if (!curr) return;
    const prev = prevCurrentBranchId.current;
    prevCurrentBranchId.current = curr;
    if (prev !== undefined && prev !== curr) {
      setActiveBranchId(curr);
      sessionStorage.setItem(`${VERSIONS_BRANCH_KEY}-${datasetId}`, curr);
    }
  }, [dataset?.current_branch_id, datasetId]);

  const activeBranch =
    branches.find((b) => b.id === activeBranchId) ??
    branches.find((b) => b.id === dataset?.current_branch_id) ??
    branches[0];
  const resolvedBranchId = activeBranch?.id;

  const versionsQueryKey = ["versions", datasetId, resolvedBranchId, search, createdAfter, createdBefore];

  const { data: versions = [], isLoading: versionsLoading, isError: versionsError } = useQuery({
    queryKey: versionsQueryKey,
    queryFn: () => versioningApi.listVersions(datasetId!, {
      branchId: resolvedBranchId,
      search: search || undefined,
      createdAfter: createdAfter || undefined,
      createdBefore: createdBefore || undefined,
    }),
    enabled: !!datasetId && settings?.versioning_mode !== "off" && !!resolvedBranchId,
  });

  const deleteMutation = useMutation({
    mutationFn: (v: Version) => versioningApi.deleteVersion(datasetId!, v.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["versions", datasetId] });
      toast.success("Version deleted");
    },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Delete failed"));
    },
  });

  const pruneMutation = useMutation({
    mutationFn: () => versioningApi.pruneStorage(datasetId!),
    onSuccess: () => { toast.success("Prune job started"); },
    onError: (err: unknown) => {
      toast.error(apiErrorDetail(err, "Prune failed"));
    },
  });

  const pinMutation = useMutation({
    mutationFn: ({ version, pinned }: { version: Version; pinned: boolean }) =>
      versioningApi.updateVersion(datasetId!, version.id, { is_pinned: pinned }),
    onSuccess: (updated) => {
      qc.setQueryData<Version[]>(versionsQueryKey, (old) => {
        if (!old) return old;
        const next = old.map((v) => (v.id === updated.id ? updated : v));
        return next.sort((a, b) => {
          if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1;
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        });
      });
    },
    onError: () => { toast.error("Failed to update pin"); },
  });

  if (!datasetId) return null;

  const mode = settings?.versioning_mode ?? "off";

  if (mode === "off") {
    return (
      <div style={{
        flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        gap: 16, padding: 40, color: "var(--fg-mute)",
      }}>
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" opacity={0.4}>
          <circle cx="12" cy="4.5" r="2.5"/>
          <circle cx="4.5" cy="19.5" r="2.5"/>
          <circle cx="19.5" cy="19.5" r="2.5"/>
          <path d="M12 7v7M12 14L4.5 17M12 14L19.5 17"/>
        </svg>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--fg)", marginBottom: 6 }}>
            Version control is disabled
          </div>
          <div style={{ fontSize: 13, maxWidth: 320 }}>
            Enable it in Settings to start tracking snapshots, restore points, and branches for your datasets.
          </div>
        </div>
        <button className="btn primary" onClick={() => navigate("/settings")}>
          → Go to Settings
        </button>
      </div>
    );
  }

  const headVersionId = activeBranch?.head_version_id;
  const hasActiveFilter = !!(searchInput || createdAfter || createdBefore);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        padding: "14px 20px", borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 12, flexShrink: 0,
      }}>
        <h1 style={{ fontSize: 16, fontWeight: 600, flex: 1, margin: 0 }}>Versions</h1>
        <span className={`badge ${mode === "auto" ? "info" : "solid"}`} style={{ fontSize: 11 }}>
          {mode === "auto" ? "Auto" : "Manual"}
        </span>
        <BranchSelector
          datasetId={datasetId}
          branches={branches}
          activeBranchId={resolvedBranchId}
          currentBranchId={dataset?.current_branch_id ?? undefined}
          onSelect={handleBranchSelect}
        />
        <button className="btn sm ghost" onClick={() => setShowDiffModal(true)} disabled={versions.length < 2}>
          Compare ▾
        </button>
        <button className="btn sm ghost" onClick={() => setShowPruneConfirm(true)}>
          Prune storage
        </button>
        <button className="btn sm primary" onClick={() => setShowCreateModal(true)}>
          + Create Snapshot
        </button>
      </div>

      {/* Filter bar */}
      <div style={{
        padding: "8px 16px", borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", gap: 10, flexShrink: 0,
        background: "var(--surface-1)",
      }}>
        <input
          className="input"
          style={{ width: 220 }}
          type="search"
          placeholder="Search by name or description…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--fg-dim)", whiteSpace: "nowrap" }}>After</span>
          <input
            className="input"
            style={{ width: 140 }}
            type="date"
            value={createdAfter}
            onChange={(e) => setCreatedAfter(e.target.value)}
          />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--fg-dim)", whiteSpace: "nowrap" }}>Before</span>
          <input
            className="input"
            style={{ width: 140 }}
            type="date"
            value={createdBefore}
            onChange={(e) => setCreatedBefore(e.target.value)}
          />
        </div>
        {hasActiveFilter && (
          <button
            className="btn sm ghost"
            onClick={() => {
              setSearchInput(""); setSearch("");
              setCreatedAfter(""); setCreatedBefore("");
            }}
          >
            Clear
          </button>
        )}
      </div>

      {/* Version list */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {versionsLoading ? (
          <div style={{ padding: 24, color: "var(--fg-mute)", fontSize: 13 }}>Loading versions…</div>
        ) : versionsError ? (
          <div style={{ padding: 24, color: "var(--bad)", fontSize: 13 }}>Failed to load versions.</div>
        ) : versions.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--fg-mute)", fontSize: 13 }}>
            {hasActiveFilter
              ? "No snapshots match the current filters."
              : "No snapshots yet. Create one to start tracking changes."}
          </div>
        ) : (
          versions.map((v) => (
            <VersionCard
              key={v.id}
              version={v}
              isHead={v.id === headVersionId}
              onRestore={setRestoreTarget}
              onDelete={setDeleteTarget}
              onTogglePin={(ver, pinned) => pinMutation.mutate({ version: ver, pinned })}
            />
          ))
        )}
      </div>

      {/* Modals */}
      {showCreateModal && (
        <CreateSnapshotModal
          datasetId={datasetId}
          activeBranchId={activeBranch?.id}
          onClose={() => setShowCreateModal(false)}
        />
      )}
      {showDiffModal && (
        <DiffModal datasetId={datasetId} versions={versions} onClose={() => setShowDiffModal(false)} />
      )}
      {showPruneConfirm && (
        <ConfirmDialog
          title="Prune Version Storage"
          message="Delete backup data no longer referenced by any snapshot. This cannot be undone."
          confirmLabel="Prune"
          danger
          onConfirm={() => { pruneMutation.mutate(); setShowPruneConfirm(false); }}
          onCancel={() => setShowPruneConfirm(false)}
        />
      )}
      {deleteTarget && (
        <ConfirmDialog
          title="Delete Snapshot"
          message={`Delete snapshot "${deleteTarget.name ?? deleteTarget.id.slice(0, 8)}"? This cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => { deleteMutation.mutate(deleteTarget); setDeleteTarget(null); }}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
      {restoreTarget && (
        <RestoreConfirmModal
          datasetId={datasetId}
          version={restoreTarget}
          onClose={() => setRestoreTarget(null)}
          onSuccess={() => {
            qc.invalidateQueries({ queryKey: ["datasets"] });
            qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
            qc.invalidateQueries({ queryKey: ["versions", datasetId] });
            qc.invalidateQueries({ queryKey: ["branches", datasetId] });
            qc.invalidateQueries({ queryKey: ["images", datasetId] });
            qc.invalidateQueries({ queryKey: ["image"] });
            qc.invalidateQueries({ queryKey: ["caption"] });
            qc.invalidateQueries({ queryKey: ["dataset-stats", datasetId] });
            qc.invalidateQueries({ queryKey: ["tag-stats", datasetId] });
            qc.invalidateQueries({ queryKey: ["score-values", datasetId] });
            qc.invalidateQueries({ queryKey: ["tag-cooccurrence", datasetId] });
          }}
        />
      )}
    </div>
  );
}
