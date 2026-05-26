import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { versioningApi } from "../../api/versioning";
import BranchSelector from "./BranchSelector";
import RestoreConfirmModal from "./RestoreConfirmModal";
import { VERSIONS_BRANCH_KEY } from "../../constants/storage";
import type { Branch, Version } from "../../types";

interface Props {
  datasetId: string;
  branches: Branch[];
  activeBranch: Branch;
  currentBranchId: string | undefined;
}

function relativeDate(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

export default function SidebarVersionPanel({ datasetId, branches, activeBranch, currentBranchId }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<Version | null>(null);
  const qc = useQueryClient();

  const { data: versions = [] } = useQuery({
    queryKey: ["versions", datasetId, activeBranch.id, "sidebar"],
    queryFn: () => versioningApi.listVersions(datasetId, { branchId: activeBranch.id, limit: 7 }),
    enabled: expanded,
    staleTime: 30_000,
  });

  const headVersionId = activeBranch.head_version_id;
  const headLabel = activeBranch.head_version_name ?? (activeBranch.head_version_id ? activeBranch.head_version_id.slice(0, 8) : null);

  function handleRestoreSuccess() {
    qc.invalidateQueries({ queryKey: ["branches", datasetId] });
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["images", datasetId] });
    qc.invalidateQueries({ queryKey: ["image"] });
    qc.invalidateQueries({ queryKey: ["caption"] });
    qc.invalidateQueries({ queryKey: ["versions", datasetId] });
  }

  return (
    <>
      <div style={{
        margin: "2px 0 6px",
        borderRadius: "var(--r)",
        border: "1px solid var(--line)",
        background: "var(--surface-2)",
        overflow: "hidden",
        marginLeft: -2,
      }}>
        {/* Header row — toggle */}
        <button
          onClick={() => setExpanded((v) => !v)}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            width: "100%", padding: "5px 10px",
            background: "none", border: "none", cursor: "pointer",
            fontSize: 11, color: "var(--fg-dim)", textAlign: "left",
            borderLeft: "2px solid transparent",
          }}
        >
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" style={{ flexShrink: 0, opacity: 0.6 }}>
            <circle cx="5" cy="3.5" r="1.8"/>
            <circle cx="11" cy="3.5" r="1.8"/>
            <circle cx="5" cy="12.5" r="1.8"/>
            <path d="M5 5.3v5.4M11 5.3c0 3-1.8 4.8-6 5.4"/>
          </svg>
          <span style={{ fontFamily: "Geist Mono, monospace", flexShrink: 0, color: "var(--fg-mute)" }}>
            {activeBranch.name}
          </span>
          {headLabel && (
            <>
              <span style={{ opacity: 0.35, flexShrink: 0 }}>·</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                {headLabel}
              </span>
            </>
          )}
          <svg
            width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6"
            style={{ flexShrink: 0, opacity: 0.5, transform: expanded ? "rotate(180deg)" : "none", transition: "transform .15s" }}
          >
            <path d="M2 3.5l3 3 3-3"/>
          </svg>
        </button>

        {/* Expanded body */}
        {expanded && (
          <div style={{
            borderTop: "1px solid var(--line)",
            padding: "10px 10px 8px",
            display: "flex", flexDirection: "column", gap: 10,
          }}>
            {/* Branch section */}
            <div>
              <div style={{ fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase", color: "var(--fg-dim)", marginBottom: 6 }}>
                Branch
              </div>
              <BranchSelector
                datasetId={datasetId}
                branches={branches}
                activeBranchId={activeBranch.id}
                currentBranchId={currentBranchId}
                onSelect={(branchId) => {
                  sessionStorage.setItem(`${VERSIONS_BRANCH_KEY}-${datasetId}`, branchId);
                }}
              />
            </div>

            {/* Snapshots section */}
            <div>
              <div style={{ fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase", color: "var(--fg-dim)", marginBottom: 6 }}>
                Snapshots
              </div>

              {versions.length === 0 ? (
                <div style={{ fontSize: 11, color: "var(--fg-dim)", padding: "2px 0" }}>No snapshots yet</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {versions.map((v) => {
                    const isCurrent = v.id === headVersionId;
                    return (
                      <div
                        key={v.id}
                        style={{
                          display: "flex", alignItems: "center", gap: 6,
                          fontSize: 11, color: isCurrent ? "var(--fg)" : "var(--fg-mute)",
                        }}
                      >
                        <span style={{
                          width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                          background: isCurrent ? "var(--accent)" : "transparent",
                          border: isCurrent ? "none" : "1px solid var(--fg-dim)",
                        }} />
                        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {v.name ?? `Snapshot ${v.id.slice(0, 8)}`}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--fg-dim)", flexShrink: 0 }}>
                          {relativeDate(v.created_at)}
                        </span>
                        {isCurrent ? (
                          <span style={{
                            fontSize: 10, color: "var(--accent)", flexShrink: 0,
                            fontFamily: "Geist Mono, monospace",
                          }}>
                            Now
                          </span>
                        ) : (
                          <button
                            className="btn sm ghost"
                            style={{ fontSize: 10, padding: "1px 6px", height: 20, flexShrink: 0 }}
                            onClick={() => setRestoreTarget(v)}
                          >
                            Restore
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Footer */}
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <Link
                to={`/datasets/${datasetId}/versions`}
                style={{ fontSize: 11, color: "var(--accent)", textDecoration: "none" }}
                onClick={() => setExpanded(false)}
              >
                View all →
              </Link>
            </div>
          </div>
        )}
      </div>

      {restoreTarget && (
        <RestoreConfirmModal
          datasetId={datasetId}
          version={restoreTarget}
          onClose={() => setRestoreTarget(null)}
          onSuccess={handleRestoreSuccess}
        />
      )}
    </>
  );
}
