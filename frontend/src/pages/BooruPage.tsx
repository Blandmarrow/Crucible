import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { booruApi } from "../api/booru";
import { copyText } from "../utils/clipboard";

const CATEGORY_COLORS: Record<string, string> = {
  character: "var(--info)",
  artist: "#a78bfa",
  copyright: "var(--warn)",
  meta: "var(--fg-dim)",
  general: "var(--fg-mute)",
};

type Source = "safebooru" | "gelbooru" | "danbooru" | "e621";
const SOURCES: { value: Source; label: string; supported: boolean }[] = [
  { value: "safebooru", label: "Safebooru", supported: true },
  { value: "gelbooru",  label: "Gelbooru",  supported: true },
  { value: "danbooru",  label: "Danbooru",  supported: false },
  { value: "e621",      label: "e621",      supported: false },
];

export default function BooruPage() {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<Source>("safebooru");
  const [limit, setLimit] = useState(50);
  const [search, setSearch] = useState("");

  const { data: tags = [], isLoading, error } = useQuery({
    queryKey: ["booru-search", search, source, limit],
    queryFn: () => booruApi.search(search, source as "safebooru" | "gelbooru", limit),
    enabled: search.length > 0,
  });

  const handleSearch = () => {
    const s = SOURCES.find((s) => s.value === source);
    if (s && !s.supported) {
      toast(`${s.label} is not yet supported`);
      return;
    }
    if (query.trim()) setSearch(query.trim());
  };

  return (
    <div style={{ padding: "24px 28px", overflowY: "auto", flex: 1 }}>
      <div className="page-h" style={{ marginBottom: 20 }}>
        <div>
          <h1>Booru tags</h1>
          <p>Look up tag names and post counts from image boards.</p>
        </div>
      </div>

      {/* Search bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <div className="search-wrap" style={{ flex: 1 }}>
          <svg className="search-ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
            <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
          </svg>
          <input
            className="input"
            placeholder="Search tags — e.g. 1girl, sword, long_hair…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            style={{ width: "100%" }}
          />
        </div>
        <select className="select" style={{ width: 130 }} value={source} onChange={(e) => setSource(e.target.value as Source)}>
          {SOURCES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}{!s.supported ? " *" : ""}</option>
          ))}
        </select>
        <select className="select" style={{ width: 80 }} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          <option value={20}>20</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
        <button className="btn primary" onClick={handleSearch}>
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>
          </svg>
          Search
        </button>
      </div>

      {/* Empty / loading state */}
      {!search && (
        <div className="empty-state" style={{ marginTop: 60 }}>
          <svg width="48" height="48" viewBox="0 0 16 16" fill="none" stroke="var(--fg-soft)" strokeWidth="1.1">
            <circle cx="7" cy="7" r="5.5"/><path d="M11.5 11.5l3 3"/>
          </svg>
          <p style={{ color: "var(--fg-dim)", fontSize: 13, marginTop: 8 }}>Enter a tag query and press Search</p>
        </div>
      )}

      {isLoading && <p style={{ color: "var(--fg-mute)", fontSize: 13 }}>Searching {source}…</p>}
      {error && <p style={{ color: "var(--bad)", fontSize: 13 }}>Search failed. Check your connection.</p>}
      {!isLoading && search && tags.length === 0 && (
        <p style={{ color: "var(--fg-mute)", fontSize: 13 }}>No tags found for "{search}"</p>
      )}

      {/* Results table */}
      {tags.length > 0 && (
        <div className="panel" style={{ overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)" }}>
                {["Tag", "Category", "Posts", ""].map((h) => (
                  <th key={h} style={{ padding: "10px 14px", textAlign: h === "Posts" ? "right" : "left", fontSize: 10.5, color: "var(--fg-dim)", fontWeight: 500, letterSpacing: ".04em", textTransform: "uppercase" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tags.map((tag) => (
                <tr key={tag.tag} style={{ borderBottom: "1px solid var(--line)" }}
                  onMouseEnter={(e) => (e.currentTarget as HTMLElement).style.background = "var(--surface-2)"}
                  onMouseLeave={(e) => (e.currentTarget as HTMLElement).style.background = ""}
                >
                  <td style={{ padding: "9px 14px" }}>
                    <span className="mono" style={{ color: CATEGORY_COLORS[tag.category] ?? "var(--fg-mute)", fontSize: 12.5 }}>{tag.tag}</span>
                  </td>
                  <td style={{ padding: "9px 14px" }}>
                    <span className={`badge ${tag.category === "character" ? "info" : tag.category === "copyright" ? "warn" : "solid"} dot`}>{tag.category}</span>
                  </td>
                  <td style={{ padding: "9px 14px", textAlign: "right", fontFamily: "Geist Mono, monospace", fontSize: 12, color: "var(--fg-mute)" }}>
                    {tag.count.toLocaleString()}
                  </td>
                  <td style={{ padding: "9px 14px" }}>
                    <button
                      className="btn ghost sm"
                      style={{ fontSize: 11.5 }}
                      onClick={() => void copyText(tag.tag).then((ok) => ok
                        ? toast.success(`Copied "${tag.tag}"`)
                        : toast.error("Could not copy to the clipboard"))}
                    >
                      Copy
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {search && (
        <p style={{ marginTop: 14, fontSize: 11.5, color: "var(--fg-soft)" }}>
          Tags sourced from <span style={{ color: "var(--fg-dim)", textTransform: "capitalize" }}>{source}</span>. Results cached for 5 minutes.
          {SOURCES.some((s) => !s.supported) && <> · * = not yet supported</>}
        </p>
      )}
    </div>
  );
}
