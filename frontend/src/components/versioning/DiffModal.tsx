import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { versioningApi } from "../../api/versioning";
import type { Version } from "../../types";

interface Props {
  datasetId: string;
  versions: Version[];
  onClose: () => void;
}

function Section({
  title, color, count, children, defaultOpen,
}: {
  title: string; color: string; count: number; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  if (count === 0) return null;
  return (
    <div style={{ border: `1px solid ${color}33`, borderRadius: "var(--r)", overflow: "hidden" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", background: `${color}15`, border: "none", cursor: "pointer",
          color: "var(--fg)", fontSize: 13, fontWeight: 500, textAlign: "left",
        }}
      >
        <span style={{
          width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0,
        }} />
        {title}
        <span style={{ marginLeft: "auto", fontSize: 12, opacity: 0.7 }}>{count}</span>
        <span style={{ fontSize: 11, opacity: 0.5 }}>{open ? "▲" : "▼"}</span>
      </button>
      {open && <div style={{ padding: "8px 12px" }}>{children}</div>}
    </div>
  );
}

export default function DiffModal({ datasetId, versions, onClose }: Props) {
  const [v1Id, setV1Id] = useState(versions[1]?.id ?? "");
  const [v2Id, setV2Id] = useState(versions[0]?.id ?? "");

  const { data: diff, isLoading, error } = useQuery({
    queryKey: ["diff", datasetId, v1Id, v2Id],
    queryFn: () => versioningApi.diff(datasetId, v1Id, v2Id),
    enabled: !!v1Id && !!v2Id && v1Id !== v2Id,
    staleTime: 60_000,
  });

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
    }}>
      <div className="panel" style={{ width: 620, maxHeight: "80vh", display: "flex", flexDirection: "column", padding: 0 }}>
        <div className="panel-h" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Compare Versions</span>
          <button className="btn ghost" onClick={onClose} style={{ padding: "2px 8px" }}>✕</button>
        </div>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 11, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>Base (A)</label>
            <select className="select" style={{ width: "100%" }} value={v1Id} onChange={(e) => setV1Id(e.target.value)}>
              <option value="">— select —</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>{v.name ?? v.id.slice(0, 8)} · {new Date(v.created_at).toLocaleDateString()}</option>
              ))}
            </select>
          </div>
          <div style={{ paddingTop: 16, color: "var(--fg-mute)" }}>→</div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 11, color: "var(--fg-mute)", display: "block", marginBottom: 4 }}>Compare (B)</label>
            <select className="select" style={{ width: "100%" }} value={v2Id} onChange={(e) => setV2Id(e.target.value)}>
              <option value="">— select —</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>{v.name ?? v.id.slice(0, 8)} · {new Date(v.created_at).toLocaleDateString()}</option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
          {!v1Id || !v2Id || v1Id === v2Id ? (
            <p style={{ color: "var(--fg-mute)", fontSize: 13, textAlign: "center", padding: "24px 0" }}>
              Select two different versions to compare.
            </p>
          ) : isLoading ? (
            <p style={{ color: "var(--fg-mute)", fontSize: 13 }}>Loading diff…</p>
          ) : error ? (
            <p style={{ color: "var(--bad)", fontSize: 13 }}>Failed to load diff.</p>
          ) : diff ? (
            <>
              <div style={{ display: "flex", gap: 12, marginBottom: 4 }}>
                {[
                  { label: "Added", count: diff.summary.added, color: "#4caf50" },
                  { label: "Removed", count: diff.summary.removed, color: "#f44336" },
                  { label: "Modified", count: diff.summary.modified, color: "#ff9800" },
                  { label: "Unchanged", count: diff.summary.unchanged, color: "var(--fg-mute)" },
                ].map((s) => (
                  <div key={s.label} style={{ textAlign: "center", flex: 1 }}>
                    <div style={{ fontSize: 20, fontWeight: 600, color: s.color }}>{s.count}</div>
                    <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>{s.label}</div>
                  </div>
                ))}
              </div>

              <Section title="Added" color="#4caf50" count={diff.added.length} defaultOpen={diff.added.length > 0}>
                {diff.added.map((item, i) => (
                  <div key={i} style={{ fontSize: 12, padding: "3px 0", borderBottom: "1px solid var(--line)", display: "flex", gap: 8 }}>
                    <span style={{ fontFamily: "Geist Mono, monospace", flex: 1 }}>{item.filename}</span>
                    {item.subfolder && <span style={{ color: "var(--fg-mute)" }}>{item.subfolder}</span>}
                  </div>
                ))}
              </Section>

              <Section title="Removed" color="#f44336" count={diff.removed.length} defaultOpen={diff.removed.length > 0}>
                {diff.removed.map((item, i) => (
                  <div key={i} style={{ fontSize: 12, padding: "3px 0", borderBottom: "1px solid var(--line)", display: "flex", gap: 8 }}>
                    <span style={{ fontFamily: "Geist Mono, monospace", flex: 1 }}>{item.filename}</span>
                    {item.subfolder && <span style={{ color: "var(--fg-mute)" }}>{item.subfolder}</span>}
                  </div>
                ))}
              </Section>

              <Section title="Modified" color="#ff9800" count={diff.modified.length} defaultOpen={diff.modified.length > 0}>
                {diff.modified.map((item, i) => (
                  <div key={i} style={{ fontSize: 12, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                    <div style={{ fontFamily: "Geist Mono, monospace", marginBottom: 3 }}>{item.filename}</div>
                    {Object.entries(item.changes).map(([field, change]) => (
                      <div key={field} style={{ fontSize: 11, color: "var(--fg-mute)", paddingLeft: 12 }}>
                        <span style={{ color: "var(--fg)" }}>{field}</span>: {JSON.stringify(change.from).slice(0, 40)} → {JSON.stringify(change.to).slice(0, 40)}
                      </div>
                    ))}
                  </div>
                ))}
              </Section>
            </>
          ) : null}
        </div>
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--line)", display: "flex", justifyContent: "flex-end" }}>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
