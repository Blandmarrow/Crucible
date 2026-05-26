import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { captionsApi, type BulkEditRequest } from "../../api/captions";
import { FLAG_OPTIONS } from "../../constants/flags";

type Operation = BulkEditRequest["operation"];

interface Props {
  datasetId: string;
  imageIds?: string[];
  /** When provided, use these flags and hide the internal flag selector. */
  qualityFlags?: string[];
  subfolder?: string;
  disabled?: boolean;
  onSuccess?: (affected: number, skipped: number) => void;
  onCancel?: () => void;
}

export default function BulkEditForm({ datasetId, imageIds, qualityFlags, subfolder, disabled, onSuccess, onCancel }: Props) {
  const qc = useQueryClient();
  const [operation, setOperation] = useState<Operation>("append");
  const [text, setText] = useState("");
  const [replacement, setReplacement] = useState("");
  const [useRegex, setUseRegex] = useState(false);
  const [internalFlags, setInternalFlags] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<{ affected: number; skipped: number } | null>(null);

  const activeFlags = qualityFlags ?? [...internalFlags];

  const toggleFlag = (key: string) => {
    setInternalFlags(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const mutation = useMutation({
    mutationFn: () => {
      const req: BulkEditRequest = {
        operation,
        text,
        replacement: operation === "find_replace" ? replacement : "",
        use_regex: useRegex,
        image_ids: imageIds,
        quality_flags: activeFlags.length > 0 ? activeFlags : undefined,
        subfolder,
      };
      return captionsApi.bulkEdit(datasetId, req);
    },
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["images", datasetId] });
      setResult(data);
      onSuccess?.(data.affected, data.skipped);
      if (data.affected === 0) {
        toast("No captions matched — nothing was changed");
      } else {
        toast.success(`Updated ${data.affected} caption${data.affected !== 1 ? "s" : ""}`);
      }
    },
    onError: () => toast.error("Bulk edit failed"),
  });

  const canSubmit = !disabled && text.trim().length > 0 && !mutation.isPending;

  const scopeLabel = imageIds != null
    ? `${imageIds.length} selected image${imageIds.length !== 1 ? "s" : ""}`
    : "all images in dataset";

  return (
    <div className="space-y-4">
      {/* Operation selector */}
      <div>
        <label className="label">Operation</label>
        <div className="flex gap-1.5 flex-wrap">
          {(["prepend", "append", "remove", "find_replace"] as Operation[]).map(op => (
            <button
              key={op}
              className={`btn btn-sm ${operation === op ? "btn-primary" : "btn-secondary"}`}
              onClick={() => { setOperation(op); setResult(null); }}
            >
              {op === "find_replace" ? "Find & Replace" : op.charAt(0).toUpperCase() + op.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Text inputs */}
      {(operation === "prepend" || operation === "append") && (
        <div>
          <label className="label">
            Text to {operation === "prepend" ? "add at start" : "add at end"}
          </label>
          <input
            className="input w-full"
            value={text}
            onChange={e => { setText(e.target.value); setResult(null); }}
            placeholder={operation === "prepend" ? "e.g. high quality," : "e.g. , masterpiece"}
            autoFocus
          />
        </div>
      )}

      {operation === "remove" && (
        <div className="space-y-2">
          <div>
            <label className="label">Text to remove</label>
            <input
              className="input w-full"
              value={text}
              onChange={e => { setText(e.target.value); setResult(null); }}
              placeholder="e.g. low quality,"
              autoFocus
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="checkbox" checked={useRegex} onChange={e => setUseRegex(e.target.checked)} />
            Use regular expression
          </label>
        </div>
      )}

      {operation === "find_replace" && (
        <div className="space-y-2">
          <div>
            <label className="label">Find</label>
            <input
              className="input w-full"
              value={text}
              onChange={e => { setText(e.target.value); setResult(null); }}
              placeholder="Text to find…"
              autoFocus
            />
          </div>
          <div>
            <label className="label">Replace with</label>
            <input
              className="input w-full"
              value={replacement}
              onChange={e => { setReplacement(e.target.value); setResult(null); }}
              placeholder="Replacement text (leave empty to delete)"
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input type="checkbox" checked={useRegex} onChange={e => setUseRegex(e.target.checked)} />
            Use regular expression
          </label>
        </div>
      )}

      {/* Internal flag filter — only shown when qualityFlags is not controlled externally */}
      {qualityFlags === undefined && (
        <div>
          <label className="label">Exclude images with flags (optional)</label>
          <div className="flex flex-wrap gap-1.5">
            {FLAG_OPTIONS.map(({ key, label }) => (
              <button
                key={key}
                className={`btn btn-sm ${internalFlags.has(key) ? "btn-primary" : "btn-secondary"}`}
                onClick={() => toggleFlag(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {internalFlags.size > 0 && (
            <p className="text-xs text-gray-500 mt-1">
              Will skip images flagged as: {[...internalFlags].map(f => FLAG_OPTIONS.find(o => o.key === f)?.label).join(" or ")}
            </p>
          )}
        </div>
      )}

      {/* Scope info */}
      <p className="text-xs text-gray-500">
        Scope: <span className="text-gray-400">{scopeLabel}</span>
        {activeFlags.length > 0 && ", excluding flagged images"}
      </p>

      {/* Result */}
      {result && (
        <div className="flex items-center gap-2 text-sm">
          <span className="badge badge-good">{result.affected} updated</span>
          {result.skipped > 0 && <span className="badge">{result.skipped} skipped</span>}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 justify-end pt-1">
        {onCancel && (
          <button className="btn-ghost" onClick={onCancel} disabled={mutation.isPending}>
            Cancel
          </button>
        )}
        <button
          className="btn-primary"
          onClick={() => mutation.mutate()}
          disabled={!canSubmit}
        >
          {mutation.isPending ? "Applying…" : "Apply"}
        </button>
      </div>
    </div>
  );
}
