import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Cpu, Copy, Check } from "lucide-react";
import toast from "react-hot-toast";
import { copyText } from "../../utils/clipboard";
import type { GenerationMetadata as GenMeta } from "../../types";

interface Props {
  metadata: GenMeta;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  // `copyText` rather than `navigator.clipboard`, which is absent on an insecure
  // origin (issue #93). There was no `.catch()` here at all, so a failed copy
  // became an unhandled rejection that popped the global error console — which is
  // why a failed copy on `0.0.0.0` looked like a crash.
  const copy = () => {
    void copyText(text).then((ok) => {
      if (!ok) { toast.error("Could not copy to the clipboard"); return; }
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button onClick={copy} className="icon-btn" title="Copy to clipboard" style={{ width: 20, height: 20 }}>
      {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
    </button>
  );
}

export default function GenerationMetadata({ metadata }: Props) {
  const [open, setOpen] = useState(true);
  const [workflowOpen, setWorkflowOpen] = useState(false);

  const hasStructured = metadata.prompt || metadata.steps || metadata.model || metadata.seed !== undefined;

  return (
    <div className="border-t border-gray-700/50 mt-2 pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 w-full text-left text-xs font-medium text-gray-300 uppercase tracking-wide hover:text-white transition-colors"
      >
        <Cpu size={12} className="text-accent" />
        GENERATION METADATA
        <span className="ml-auto">
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-xs">
          {metadata.source && (
            <span className="badge badge-info capitalize">{metadata.source === "a1111" ? "AUTOMATIC1111" : metadata.source === "comfyui" ? "ComfyUI" : metadata.source}</span>
          )}

          {/* Prompt */}
          {metadata.prompt && (
            <div>
              <div className="flex items-center gap-1 text-gray-500 mb-1">
                Prompt
                <CopyButton text={metadata.prompt} />
              </div>
              <p className="text-gray-200 leading-relaxed bg-surface-2 rounded p-2 whitespace-pre-wrap break-words">
                {metadata.prompt}
              </p>
            </div>
          )}

          {/* Negative prompt */}
          {metadata.negative_prompt && (
            <div>
              <div className="flex items-center gap-1 text-gray-500 mb-1">
                Negative Prompt
                <CopyButton text={metadata.negative_prompt} />
              </div>
              <p className="text-gray-400 leading-relaxed bg-surface-2 rounded p-2 whitespace-pre-wrap break-words">
                {metadata.negative_prompt}
              </p>
            </div>
          )}

          {/* Structured params grid */}
          {hasStructured && (
            <div className="grid grid-cols-2 gap-x-3 gap-y-1">
              {metadata.model && (
                <>
                  <span className="text-gray-500">Model</span>
                  <span className="truncate" title={metadata.model}>{metadata.model}</span>
                </>
              )}
              {metadata.sampler && (
                <>
                  <span className="text-gray-500">Sampler</span>
                  <span>{metadata.sampler}</span>
                </>
              )}
              {metadata.steps !== undefined && (
                <>
                  <span className="text-gray-500">Steps</span>
                  <span>{metadata.steps}</span>
                </>
              )}
              {metadata.cfg_scale !== undefined && (
                <>
                  <span className="text-gray-500">CFG Scale</span>
                  <span>{metadata.cfg_scale}</span>
                </>
              )}
              {metadata.seed !== undefined && (
                <>
                  <span className="text-gray-500">Seed</span>
                  <span className="font-mono">{metadata.seed}</span>
                </>
              )}
              {metadata.size && (
                <>
                  <span className="text-gray-500">Gen Size</span>
                  <span>{metadata.size}</span>
                </>
              )}
              {metadata.vae && (
                <>
                  <span className="text-gray-500">VAE</span>
                  <span className="truncate" title={metadata.vae}>{metadata.vae}</span>
                </>
              )}
            </div>
          )}

          {/* ComfyUI workflow */}
          {metadata.comfyui_workflow && (
            <div>
              <button
                onClick={() => setWorkflowOpen(!workflowOpen)}
                className="flex items-center gap-1 text-gray-500 hover:text-gray-300 transition-colors"
              >
                {workflowOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                Raw Workflow JSON
              </button>
              {workflowOpen && (
                <pre className="mt-1 text-gray-400 bg-surface-2 rounded p-2 overflow-auto max-h-48 text-xs leading-relaxed">
                  {JSON.stringify(metadata.comfyui_workflow, null, 2)}
                </pre>
              )}
            </div>
          )}

          {/* Fallback raw text */}
          {!hasStructured && !metadata.comfyui_workflow && metadata.raw && (
            <div>
              <div className="flex items-center gap-1 text-gray-500 mb-1">
                Raw Parameters
                <CopyButton text={metadata.raw} />
              </div>
              <pre className="text-gray-400 bg-surface-2 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap break-words">
                {metadata.raw}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
