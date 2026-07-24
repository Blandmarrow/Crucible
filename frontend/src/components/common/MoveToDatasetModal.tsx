import { useState, useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRightFromLine, Copy } from "lucide-react";
import { datasetsApi } from "../../api/datasets";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import type { Dataset, SubfolderInfo } from "../../types";

interface Props {
  count: number;
  currentDatasetId: string;
  onConfirm: (targetDatasetId: string, subfolder: string) => void;
  isPending: boolean;
  onClose: () => void;
  sourceInfo?: ReactNode;
  mode?: "move" | "copy";
}

const CUSTOM = "__custom__";

export default function MoveToDatasetModal({ count, currentDatasetId, onConfirm, isPending, onClose, sourceInfo, mode = "move" }: Props) {
  const [selectedId, setSelectedId] = useState("");
  // selectValue: "" = root/placeholder, existing path, or CUSTOM sentinel
  const [selectValue, setSelectValue] = useState("");
  const [customInput, setCustomInput] = useState("");
  const { overlayProps, panelProps } = useModalBehavior({
    onClose,
    label: `${mode === "copy" ? "Copy" : "Move"} images to dataset`,
  });

  const { data: allDatasets = [] } = useQuery<Dataset[]>({
    queryKey: ["datasets"],
    queryFn: datasetsApi.list,
    staleTime: 30_000,
  });
  const choices = allDatasets.filter((d) => d.id !== currentDatasetId);

  const { data: targetSubfolders = [] } = useQuery<SubfolderInfo[]>({
    queryKey: ["subfolders", selectedId],
    queryFn: () => datasetsApi.subfolders(selectedId),
    enabled: !!selectedId,
  });

  useEffect(() => {
    setSelectValue("");
    setCustomInput("");
  }, [selectedId]);

  const isCustom = selectValue === CUSTOM;
  const effectiveSubfolder = isCustom ? customInput : selectValue;
  const showCustomInput = targetSubfolders.length === 0 || isCustom;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div className="card p-5 w-full max-w-sm space-y-3" {...panelProps}>
        <h4 className="font-medium flex items-center gap-2">
          {mode === "copy" ? <Copy size={15} /> : <ArrowRightFromLine size={15} />}
          {mode === "copy" ? "Copy" : "Move"} {count} Image{count !== 1 ? "s" : ""} to Dataset
        </h4>
        {sourceInfo}

        {choices.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--fg-mute)" }}>No other datasets available.</p>
        ) : (
          <div style={{ maxHeight: 220, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
            {choices.map((d) => (
              <button
                key={d.id}
                className={`btn btn-sm ${selectedId === d.id ? "primary" : ""}`}
                style={{ justifyContent: "space-between", textAlign: "left" }}
                onClick={() => setSelectedId(d.id)}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</span>
                <span style={{ fontSize: 11, color: "var(--fg-mute)", flexShrink: 0, marginLeft: 8 }}>
                  {d.image_count} image{d.image_count !== 1 ? "s" : ""}
                </span>
              </button>
            ))}
          </div>
        )}

        {targetSubfolders.length > 0 && (
          <select
            className="select"
            value={selectValue}
            onChange={(e) => setSelectValue(e.target.value)}
          >
            <option value="">— root (no subfolder) —</option>
            {targetSubfolders.filter((sf) => sf.path !== "").map((sf) => (
              <option key={sf.path} value={sf.path}>
                {sf.path} ({sf.image_count} image{sf.image_count !== 1 ? "s" : ""})
              </option>
            ))}
            <option value={CUSTOM}>New subfolder…</option>
          </select>
        )}

        {showCustomInput && (
          <input
            className="input"
            placeholder="New subfolder path (optional)"
            value={customInput}
            onChange={(e) => setCustomInput(e.target.value)}
            autoFocus={isCustom}
          />
        )}

        <div className="flex gap-2 justify-end">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary"
            onClick={() => onConfirm(selectedId, effectiveSubfolder)}
            disabled={!selectedId || isPending}
          >
            {mode === "copy" ? "Copy" : "Move"}
          </button>
        </div>
      </div>
    </div>
  );
}
