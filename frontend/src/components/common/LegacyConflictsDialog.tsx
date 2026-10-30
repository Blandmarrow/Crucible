import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import axios from "axios";
import { comfyApi, type ComfyGenSettings } from "../../api/comfy";
import {
  applyLegacyConflict,
  dismissLegacyConflict,
  readLegacyConflicts,
  runLegacyImport,
  type LegacyConflict,
} from "../../utils/legacyImport";
import ModalShell from "./ModalShell";

interface ConflictDetail {
  conflict: LegacyConflict;
  title: string;
  thisBrowser: string;
  current: string;
}

const QUERY_KEY = ["app-data", "legacy-conflicts"];

function describeGenSettings(s: ComfyGenSettings): string {
  const settings = `batch ${s.batch_size} · temperature ${s.temperature} · target ${s.target_count}${s.model ? ` · ${s.model}` : ""}`;
  return `${s.instructions || "(no instructions)"}\n\n${settings}`;
}

async function loadDetails(): Promise<ConflictDetail[]> {
  await runLegacyImport();
  const details: ConflictDetail[] = [];
  for (const conflict of readLegacyConflicts()) {
    try {
      const [plan, current] = await Promise.all([
        comfyApi.getPlan(conflict.id),
        comfyApi.getGenSettings(conflict.id),
      ]);
      details.push({
        conflict,
        title: `Generate-prompts settings for plan “${plan.name}”`,
        thisBrowser: describeGenSettings(conflict.value),
        current: current ? describeGenSettings(current) : "(nothing saved)",
      });
    } catch (err) {
      // The plan was deleted after the import, so there is nothing left to choose between.
      if (axios.isAxiosError(err) && err.response?.status === 404) dismissLegacyConflict(conflict);
      else throw err;
    }
  }
  return details;
}

/** Asks what to do when this browser held something different from what another browser
 *  had already copied to the app. Closing it decides nothing, so it asks again next load. */
export default function LegacyConflictsDialog() {
  const qc = useQueryClient();
  const [closed, setClosed] = useState(false);
  const [busy, setBusy] = useState(false);
  const { data: details = [] } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: loadDetails,
    refetchOnWindowFocus: false,
    retry: false,
  });

  if (closed || details.length === 0) return null;
  const { conflict, title, thisBrowser, current } = details[0];

  async function resolve(useThisBrowsers: boolean) {
    setBusy(true);
    try {
      if (useThisBrowsers) await applyLegacyConflict(conflict);
      dismissLegacyConflict(conflict);
      // A form already open holds the old copy, and its next save would put it back.
      if (useThisBrowsers) {
        window.location.reload();
        return;
      }
      await qc.invalidateQueries({ queryKey: QUERY_KEY });
    } catch {
      toast.error("Couldn't save that choice. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const preStyle = {
    margin: 0, padding: 8, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word",
    maxHeight: 160, overflowY: "auto", background: "var(--surface-2)", borderRadius: 4,
  } as const;

  return (
    <ModalShell label="Different saved copies" onClose={() => setClosed(true)} style={{ width: 560, maxWidth: "94vw" }}>
      <h3 style={{ margin: "0 0 8px" }}>Different saved copies</h3>
      <p style={{ fontSize: 13, color: "var(--fg-mute)", margin: "0 0 12px" }}>
        {title} now live in the app, and another browser got there first with a different copy. Which do you want to keep?
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>In the app now</div>
          <pre style={preStyle}>{current}</pre>
        </div>
        <div>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>In this browser</div>
          <pre style={preStyle}>{thisBrowser}</pre>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost sm" onClick={() => setClosed(true)} disabled={busy}>Decide later</button>
        <button className="btn sm" onClick={() => resolve(false)} disabled={busy}>Keep the app's copy</button>
        <button className="btn primary sm" onClick={() => resolve(true)} disabled={busy}>Use this browser's copy</button>
      </div>
    </ModalShell>
  );
}
