import { NavLink, useMatch } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { datasetsApi } from "../../api/datasets";
import { versioningApi } from "../../api/versioning";
import { useGpuStats } from "../../hooks/useGpuStats";
import { useCpuRamStats } from "../../hooks/useCpuRamStats";
import SidebarVersionPanel from "../versioning/SidebarVersionPanel";
import { useErrorConsoleStore } from "../../stores/errorConsoleStore";

/* ── SVG icons matching the design spec ── */
const IcoDatasets = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <ellipse cx="8" cy="3.5" rx="5.5" ry="2"/>
    <path d="M2.5 3.5v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-4"/>
    <path d="M2.5 7.5v4c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-4"/>
  </svg>
);
const IcoBooru = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M2.5 4.5l5.5-2 5.5 2v7l-5.5 2-5.5-2v-7z"/>
    <path d="M2.5 4.5L8 6.5l5.5-2"/>
    <path d="M8 6.5v7"/>
  </svg>
);
const IcoGallery = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2" y="2.5" width="5.5" height="5.5" rx="1"/>
    <rect x="8.5" y="2.5" width="5.5" height="5.5" rx="1"/>
    <rect x="2" y="9" width="5.5" height="5" rx="1"/>
    <rect x="8.5" y="9" width="5.5" height="5" rx="1"/>
  </svg>
);
const IcoCaptioning = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M3 5l1.4 1.4L3 7.8M3 9.5h4M9 3.5l1 2.5 2.5 1-2.5 1L9 10.5 8 8l-2.5-1L8 6l1-2.5z"/>
  </svg>
);
const IcoQuality = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
    <path d="M8 1.5l1.9 4 4.1.6-3 2.9.7 4.1L8 11.2l-3.7 1.9.7-4.1-3-2.9 4.1-.6L8 1.5z"/>
  </svg>
);
const IcoStats = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M2.5 13.5h11M4.5 13V8M7.5 13V4M10.5 13V9.5M13.5 13V6.5"/>
  </svg>
);
const IcoExport = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M8 2v8M5 7l3 3 3-3M2.5 13.5h11"/>
  </svg>
);
const IcoBulkEdit = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M3 8h7M3 5h10M3 11h5"/>
    <path d="M11.5 10l1.5-1.5 1.5 1.5-3 3-1.5.5.5-1.5z"/>
  </svg>
);
const IcoConsolidate = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M3 3v3a3 3 0 0 0 3 3h4a3 3 0 0 1 3 3v1"/>
    <path d="M11 7l2-2-2-2"/>
  </svg>
);
const IcoVersions = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <circle cx="8" cy="3" r="1.5"/>
    <circle cx="3" cy="13" r="1.5"/>
    <circle cx="13" cy="13" r="1.5"/>
    <path d="M8 4.5v4M8 8.5L3 11.5M8 8.5L13 11.5"/>
  </svg>
);
const IcoComfy = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2" y="3" width="5" height="4" rx="1"/>
    <rect x="9" y="9" width="5" height="4" rx="1"/>
    <path d="M7 5h3.5A1.5 1.5 0 0 1 12 6.5V9M9 11H5.5A1.5 1.5 0 0 1 4 9.5V7"/>
  </svg>
);
const IcoFileBrowser = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <path d="M2.5 4.5h4l1.5 1.5h5.5v8h-11v-9.5z"/>
    <path d="M2.5 7h11"/>
  </svg>
);
const IcoSettings = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <circle cx="8" cy="8" r="2.2"/>
    <path d="M8 1.5v1.3M8 13.2v1.3M1.5 8h1.3M13.2 8h1.3M3.4 3.4l.9.9M11.7 11.7l.9.9M3.4 12.6l.9-.9M11.7 4.3l.9-.9"/>
  </svg>
);
const IcoLogs = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/>
    <path d="M5 5.5h6M5 8h6M5 10.5h4"/>
  </svg>
);
const IcoCpu = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="4" y="4" width="8" height="8" rx="1"/>
    <path d="M6 4V2M8 4V2M10 4V2M6 14v-2M8 14v-2M10 14v-2M4 6H2M4 8H2M4 10H2M14 6h-2M14 8h-2M14 10h-2"/>
  </svg>
);
const IcoRam = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="1.5" y="5" width="13" height="6" rx="1"/>
    <path d="M4.5 5V3.5M7 5V3.5M9.5 5V3.5M12 5V3.5M4.5 11v1.5M7 11v1.5M9.5 11v1.5M12 11v1.5"/>
  </svg>
);
const IcoGpu = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
    <rect x="2.5" y="3.5" width="11" height="9" rx="1"/>
    <path d="M5 6.5h6M5 9h4"/>
  </svg>
);

/* ── Hardware meter row ── */
function MeterRow({ icon, label, value, pct }: {
  icon: React.ReactNode;
  label: string;
  value: string | null;
  pct: number | null;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      {icon}
      <div style={{ flex: 1 }}>
        {value != null ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
              <span style={{ color: "var(--fg-mute)" }}>{label}</span>
              <span className="mono" style={{ color: "var(--fg-dim)" }}>{value}</span>
            </div>
            <div style={{ height: 3, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden", marginTop: 4 }}>
              <div style={{
                height: "100%", background: "var(--accent)",
                width: `${pct}%`, borderRadius: 2,
              }} />
            </div>
          </>
        ) : (
          <span style={{ color: "var(--fg-soft)", fontSize: 11 }}>No {label} data</span>
        )}
      </div>
    </div>
  );
}

/* ── Nav item ── */
function NavItem({
  to,
  icon,
  label,
  tail,
  tailColor,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
  tail?: React.ReactNode;
  tailColor?: string;
}) {
  return (
    <NavLink
      to={to}
      style={({ isActive }) => ({
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "7px 10px",
        borderRadius: "var(--r)",
        color: isActive ? "var(--fg)" : "var(--fg-mute)",
        background: isActive ? "var(--surface-3)" : "transparent",
        fontSize: 13,
        cursor: "pointer",
        userSelect: "none" as const,
        transition: "background .12s, color .12s",
        textDecoration: "none",
        position: "relative" as const,
        borderLeft: isActive ? "2px solid var(--accent)" : "2px solid transparent",
        marginLeft: -2,
      })}
      onMouseEnter={(e) => {
        const el = e.currentTarget as HTMLElement;
        if (!el.style.background.includes("surface-3")) {
          el.style.background = "var(--surface-2)";
          el.style.color = "var(--fg)";
        }
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget as HTMLElement;
        if (!el.classList.contains("active")) {
          el.style.background = "";
          el.style.color = "";
        }
      }}
    >
      <span style={{ opacity: 0.85, flexShrink: 0 }}>{icon}</span>
      <span style={{ flex: 1 }}>{label}</span>
      {tail && (
        <span style={{
          fontSize: 11, color: tailColor ?? "var(--fg-dim)",
          background: "var(--surface-2)", padding: "1px 6px",
          borderRadius: 3, border: "1px solid var(--line)",
          fontFamily: "Geist Mono, monospace",
        }}>
          {tail}
        </span>
      )}
    </NavLink>
  );
}

export default function Sidebar() {
  const match = useMatch("/datasets/:datasetId/*");
  const datasetId = match?.params?.datasetId;
  const gpu = useGpuStats();
  const cpuRam = useCpuRamStats();
  const errorCount = useErrorConsoleStore((s) => s.errors.length);

  const { data: dataset } = useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => datasetsApi.get(datasetId!),
    enabled: !!datasetId,
    staleTime: 30_000,
  });

  const { data: branches = [] } = useQuery({
    queryKey: ["branches", datasetId],
    queryFn: () => versioningApi.listBranches(datasetId!),
    enabled: !!datasetId,
    staleTime: 30_000,
  });

  const activeBranch = branches.find((b) => b.id === dataset?.current_branch_id) ?? branches[0];

  const imgCount = dataset?.image_count;

  return (
    <aside style={{
      background: "var(--surface-1)",
      borderRight: "1px solid var(--line)",
      display: "flex", flexDirection: "column",
      height: "100%", minWidth: 0,
    }}>
      {/* Brand */}
      <div style={{
        padding: "14px 16px", display: "flex", alignItems: "center", gap: 10,
        borderBottom: "1px solid var(--line)", height: 49, flexShrink: 0,
      }}>
        <div style={{
          width: 22, height: 22, borderRadius: 5, flexShrink: 0,
          background: "radial-gradient(circle at 30% 30%, var(--accent-2), var(--accent) 60%, var(--accent-deep) 110%)",
          boxShadow: "0 0 0 1px var(--line-2)",
        }} />
        <div>
          <div style={{ fontWeight: 600, letterSpacing: "-0.01em", fontSize: 14 }}>Crucible</div>
          <div style={{ color: "var(--fg-dim)", fontSize: 11, marginTop: 1, letterSpacing: ".02em", fontFamily: "Geist Mono, monospace" }}>
            local
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ padding: "10px 8px 10px 10px", display: "flex", flexDirection: "column", gap: 1, flex: 1, overflowY: "auto" }}>
        <NavItem to="/datasets" icon={<IcoDatasets />} label="Datasets" />
        <NavItem to="/booru" icon={<IcoBooru />} label="Booru Browser" />
        <NavItem to="/file-browser" icon={<IcoFileBrowser />} label="File Browser" />
        <NavItem to="/settings" icon={<IcoSettings />} label="Settings" />
        <NavItem
          to="/logs"
          icon={<IcoLogs />}
          label="Logs"
          tail={errorCount > 0 ? errorCount : undefined}
          tailColor={errorCount > 0 ? "var(--bad)" : undefined}
        />

        {datasetId && (
          <>
            <div style={{
              padding: "14px 8px 4px", fontSize: 10, letterSpacing: ".12em",
              textTransform: "uppercase", color: "var(--fg-dim)",
            }}>
              Active dataset
            </div>

            {activeBranch && (
              <SidebarVersionPanel
                datasetId={datasetId}
                branches={branches}
                activeBranch={activeBranch}
                currentBranchId={dataset?.current_branch_id ?? undefined}
              />
            )}

            <NavItem
              to={`/datasets/${datasetId}/gallery`}
              icon={<IcoGallery />}
              label="Gallery"
              tail={imgCount != null ? imgCount.toLocaleString() : undefined}
            />
            <NavItem to={`/datasets/${datasetId}/captioning`} icon={<IcoCaptioning />} label="Captioning" />
            <NavItem to={`/datasets/${datasetId}/quality`} icon={<IcoQuality />} label="Score images" />
            <NavItem to={`/datasets/${datasetId}/stats`} icon={<IcoStats />} label="Stats" />
            <NavItem to={`/datasets/${datasetId}/bulk-edit`} icon={<IcoBulkEdit />} label="Bulk Edit" />
            <NavItem to={`/datasets/${datasetId}/consolidate`} icon={<IcoConsolidate />} label="Consolidate Tags" />
            <NavItem to={`/datasets/${datasetId}/versions`} icon={<IcoVersions />} label="Versions" />
            <NavItem to={`/datasets/${datasetId}/comfy`} icon={<IcoComfy />} label="ComfyUI" />
            <NavItem to={`/datasets/${datasetId}/export`} icon={<IcoExport />} label="Export" />
          </>
        )}
      </nav>

      {/* Hardware stats footer */}
      <div style={{
        borderTop: "1px solid var(--line)", padding: "10px 12px",
        display: "flex", flexDirection: "column", gap: 8,
        color: "var(--fg-mute)", fontSize: 12, flexShrink: 0,
      }}>
        <MeterRow
          icon={<IcoCpu />}
          label="CPU"
          value={cpuRam ? `${cpuRam.cpu_pct.toFixed(1)}%` : null}
          pct={cpuRam ? Math.min(100, cpuRam.cpu_pct) : null}
        />
        <MeterRow
          icon={<IcoRam />}
          label="RAM"
          value={cpuRam ? `${(cpuRam.ram_used_mb / 1024).toFixed(1)} / ${(cpuRam.ram_total_mb / 1024).toFixed(1)} GB` : null}
          pct={cpuRam ? Math.min(100, (cpuRam.ram_used_mb / cpuRam.ram_total_mb) * 100) : null}
        />
        <MeterRow
          icon={<IcoGpu />}
          label={gpu?.name || "GPU"}
          value={gpu ? `${(gpu.used_mb / 1024).toFixed(1)} / ${(gpu.total_mb / 1024).toFixed(1)} GB` : null}
          pct={gpu ? Math.min(100, (gpu.used_mb / gpu.total_mb) * 100) : null}
        />
      </div>
    </aside>
  );
}
