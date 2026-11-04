import { useQuery } from "@tanstack/react-query";
import { comfyApi, type BlueprintCombineItem, type BlueprintSpec } from "../../api/comfy";
import { apiErrorDetail } from "../../utils/apiError";

interface Props {
  planId: string;
  draft: BlueprintSpec;
  seed: number;
}

const SAMPLE_FOLDERS = 4;
const h4 = { fontSize: 12, margin: "0 0 4px", color: "var(--fg-mute)", fontWeight: 600 } as const;
const mute = { color: "var(--fg-mute)" } as const;

function combineText(items: BlueprintCombineItem[]): string {
  return items
    .map((c) => {
      const axis = typeof c === "string" ? c : c.axis;
      const take = typeof c === "string" ? 1 : c.take;
      return take === 1 ? axis : `${axis} ×${take}`;
    })
    .join(" + ");
}

/** A read-only look at an outline draft before *Use it*: what the model invented, and
    what it would expand to. The expansion comes from the same preview endpoint the form
    uses, which reads the spec from the body and writes nothing, so a draft can be
    previewed while the saved spec is something else entirely. */
export default function DraftReview({ planId, draft, seed }: Props) {
  const draftJSON = JSON.stringify(draft);
  const { data: preview, isError, error, isPending } = useQuery({
    queryKey: ["comfy", "blueprint-draft-preview", planId, draftJSON, seed],
    queryFn: ({ signal }) => comfyApi.previewBlueprint(planId, { spec: draft, seed }, signal),
    staleTime: 0,
  });

  const axes = Object.entries(draft.axes);
  const variations = Object.entries(draft.variations);
  const sampled = (preview?.leaves ?? []).filter((l) => l.samples.length > 0).slice(0, SAMPLE_FOLDERS);

  return (
    <div data-testid="draft-review"
      style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 12, maxHeight: "60vh", overflowY: "auto", paddingRight: 4 }}>
      <div data-testid="draft-review-totals" style={{ fontSize: 13 }}>
        {isPending ? "Working out what it makes…"
          : isError ? <span style={{ color: "var(--bad)" }}>{apiErrorDetail(error, "Could not preview the draft.")}</span>
          : preview && (
            <>
              <strong>{preview.leaf_count}</strong> folder{preview.leaf_count !== 1 ? "s" : ""} ·{" "}
              <strong>{preview.rows_to_add}</strong> row{preview.rows_to_add !== 1 ? "s" : ""} once applied
            </>
          )}
      </div>
      {(preview?.errors.length ?? 0) > 0 && (
        <div>
          <ul role="alert" style={{ margin: 0, paddingLeft: 18, color: "var(--bad)" }}>
            {preview!.errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
          <div style={{ ...mute, marginTop: 2 }}>You can still use it and fix these in the editors.</div>
        </div>
      )}
      {(preview?.warnings.length ?? 0) > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, color: "var(--warn, var(--fg-mute))" }}>
          {preview!.warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}

      {axes.map(([name, entities]) => (
        <section key={name}>
          <h4 style={h4}>Axis <span className="mono">{name}</span> · {entities.length}</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {entities.map((e) => (
              <li key={e.id} style={{ marginBottom: 2 }}>
                <strong>{e.label}</strong> <span style={mute}>— {e.descriptor}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {variations.length > 0 && (
        <section>
          <h4 style={h4}>Variations</h4>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {variations.map(([name, pieces]) => (
              <li key={name}><span className="mono">{name}</span>: <span style={mute}>{pieces.join(" · ")}</span></li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h4 style={h4}>Scenarios</h4>
        {draft.scenarios.map((s) => (
          <div key={s.name} style={{ marginBottom: 6 }}>
            <div>
              <strong>{s.name}</strong>{" "}
              <span style={mute}>{combineText(s.combine)} · {s.mode} · {s.count} per folder · into</span>{" "}
              <span className="mono">{s.path}</span>
            </div>
            {s.template && <div className="mono" style={{ ...mute, marginTop: 2 }}>{s.template}</div>}
          </div>
        ))}
      </section>

      {sampled.length > 0 && (
        <section>
          <h4 style={h4}>Sample prompts</h4>
          {sampled.map((l) => (
            <div key={l.scenario + "/" + l.path} style={{ marginBottom: 6 }}>
              <div className="mono">{l.path}</div>
              <div style={mute}>{l.samples[0]}</div>
            </div>
          ))}
          {preview && preview.leaf_count > sampled.length && (
            <div style={mute}>+{preview.leaf_count - sampled.length} more folders</div>
          )}
        </section>
      )}
    </div>
  );
}
