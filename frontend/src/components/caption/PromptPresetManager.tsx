import { useState } from "react";
import { BookOpen, Save, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import type { PromptPreset } from "../../api/appData";
import { usePromptPresets } from "../../hooks/usePromptPresets";

interface Props {
  currentModel: string;
  currentStyle: string;
  currentPrompt: string;
  onLoad: (preset: PromptPreset) => void;
  defaultOpen?: boolean;
}

export default function PromptPresetManager({ currentModel, currentStyle, currentPrompt, onLoad, defaultOpen = false }: Props) {
  const { presets, save, remove } = usePromptPresets();
  const [open, setOpen] = useState(defaultOpen);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    if (!saveName.trim() || !currentModel) return;
    if (!(await save({ name: saveName.trim(), model: currentModel, style: currentStyle, prompt: currentPrompt }))) return;
    setSaveName("");
    setSaving(false);
  }

  return (
    <div className="border border-gray-700 rounded">
      <button
        type="button"
        className="flex items-center justify-between w-full px-3 py-2 text-sm text-gray-300 hover:text-white transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-2">
          <BookOpen size={13} /> Presets
          {presets.length > 0 && (
            <span className="text-xs text-gray-500">({presets.length})</span>
          )}
        </span>
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      {open && (
        <div className="border-t border-gray-700 p-3 space-y-3">
          {/* Saved presets list */}
          {presets.length === 0 ? (
            <p className="text-xs text-gray-500">No presets saved yet.</p>
          ) : (
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {presets.map((p) => (
                <div
                  key={p.id}
                  className="flex items-start gap-2 p-2 rounded border border-gray-700 hover:border-gray-500 group transition-colors"
                >
                  <button
                    type="button"
                    className="flex-1 text-left min-w-0"
                    onClick={() => onLoad(p)}
                  >
                    <div className="text-xs font-medium text-gray-200 truncate">{p.name}</div>
                    <div className="text-xs text-gray-500 truncate">
                      {p.model} · {p.style}
                      {p.prompt && <span className="italic"> · "{p.prompt.slice(0, 40)}{p.prompt.length > 40 ? "…" : ""}"</span>}
                    </div>
                  </button>
                  <button
                    type="button"
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 transition-all p-0.5 shrink-0"
                    onClick={() => remove(p.id)}
                    title="Delete preset"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Save current as preset */}
          {saving ? (
            <div className="flex gap-2">
              <input
                className="input flex-1 text-xs py-1"
                placeholder="Preset name…"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSave();
                  // Stopped so that, inside a dialog (the gallery's Caption modal),
                  // Escape closes this inline row and not the dialog around it.
                  if (e.key === "Escape") { e.stopPropagation(); setSaving(false); }
                }}
                autoFocus
              />
              <button
                type="button"
                className="btn-primary btn-sm text-xs"
                onClick={handleSave}
                disabled={!saveName.trim() || !currentModel}
              >
                Save
              </button>
              <button type="button" className="btn-ghost btn-sm text-xs" onClick={() => setSaving(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn-ghost btn-sm flex items-center gap-1.5 text-xs w-full justify-center"
              onClick={() => setSaving(true)}
              disabled={!currentModel}
              title={!currentModel ? "Select a model first" : "Save current settings as a preset"}
            >
              <Save size={12} /> Save current as preset
            </button>
          )}
        </div>
      )}
    </div>
  );
}
