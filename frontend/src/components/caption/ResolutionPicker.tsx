import { useState } from "react";

interface ResolutionPickerProps {
  targetWidth: number | null;
  targetHeight: number | null;
  onChange: (w: number | null, h: number | null) => void;
}

interface Preset {
  label: string;
  w: number | null;
  h: number | null;
}

const PRESETS: Preset[] = [
  { label: "Original (no crop)", w: null, h: null },
  { label: "512 × 512  (1:1)",   w: 512,  h: 512  },
  { label: "768 × 768  (1:1)",   w: 768,  h: 768  },
  { label: "1024 × 1024  (1:1)", w: 1024, h: 1024 },
  { label: "512 × 768  (2:3)",   w: 512,  h: 768  },
  { label: "768 × 512  (3:2)",   w: 768,  h: 512  },
  { label: "768 × 1024  (3:4)",  w: 768,  h: 1024 },
  { label: "1024 × 768  (4:3)",  w: 1024, h: 768  },
  { label: "576 × 1024  (9:16)", w: 576,  h: 1024 },
  { label: "1024 × 576  (16:9)", w: 1024, h: 576  },
  { label: "Custom…",            w: null, h: null  },
];

const CUSTOM_SENTINEL = "__custom__";

function selectValue(w: number | null, h: number | null, isCustom: boolean): string {
  if (isCustom) return CUSTOM_SENTINEL;
  if (w === null || h === null) return "original";
  return `${w}x${h}`;
}

function presetKey(p: Preset): string {
  if (p.label === "Custom…") return CUSTOM_SENTINEL;
  if (p.w === null) return "original";
  return `${p.w}x${p.h}`;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

function aspectLabel(w: number, h: number): string {
  const d = gcd(w, h);
  return `${w / d}:${h / d}`;
}

export default function ResolutionPicker({ targetWidth, targetHeight, onChange }: ResolutionPickerProps) {
  const [isCustom, setIsCustom] = useState(false);
  const [customW, setCustomW] = useState(targetWidth ?? 512);
  const [customH, setCustomH] = useState(targetHeight ?? 512);

  const currentValue = selectValue(targetWidth, targetHeight, isCustom);

  function handleSelect(e: React.ChangeEvent<HTMLSelectElement>) {
    const val = e.target.value;
    if (val === CUSTOM_SENTINEL) {
      setIsCustom(true);
      onChange(customW, customH);
    } else if (val === "original") {
      setIsCustom(false);
      onChange(null, null);
    } else {
      setIsCustom(false);
      const [w, h] = val.split("x").map(Number);
      onChange(w, h);
    }
  }

  function handleCustomW(e: React.ChangeEvent<HTMLInputElement>) {
    const w = parseInt(e.target.value) || 0;
    setCustomW(w);
    if (w > 0 && customH > 0) onChange(w, customH);
  }

  function handleCustomH(e: React.ChangeEvent<HTMLInputElement>) {
    const h = parseInt(e.target.value) || 0;
    setCustomH(h);
    if (customW > 0 && h > 0) onChange(customW, h);
  }

  const showAR = targetWidth && targetHeight;

  return (
    <div className="space-y-2">
      <label className="label">Target Resolution</label>
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="input flex-1 min-w-0"
          value={currentValue}
          onChange={handleSelect}
        >
          {PRESETS.map((p) => (
            <option key={presetKey(p)} value={presetKey(p)}>
              {p.label}
            </option>
          ))}
        </select>

        {showAR && (
          <span className="text-xs text-fg-dim shrink-0">
            {aspectLabel(targetWidth!, targetHeight!)}
          </span>
        )}
      </div>

      {isCustom && (
        <div className="flex items-center gap-2">
          <input
            type="number"
            className="input w-24 text-center"
            min={64}
            max={4096}
            step={64}
            value={customW}
            onChange={handleCustomW}
            placeholder="W"
          />
          <span className="text-fg-dim text-sm">×</span>
          <input
            type="number"
            className="input w-24 text-center"
            min={64}
            max={4096}
            step={64}
            value={customH}
            onChange={handleCustomH}
            placeholder="H"
          />
          {customW > 0 && customH > 0 && (
            <span className="text-xs text-fg-dim">
              {aspectLabel(customW, customH)}
            </span>
          )}
        </div>
      )}

      {(targetWidth || targetHeight) && (
        <p className="text-xs text-fg-dim">
          Images will be center-cropped to this aspect ratio before captioning.
        </p>
      )}
    </div>
  );
}
