import { useState } from "react";
import type { BlueprintSpec } from "../../api/comfy";
import BlueprintNameField from "./BlueprintNameField";
import BlueprintSection, { type SectionControl, type SectionRef } from "./BlueprintSection";
import { BLUEPRINT_LIMITS, freshName } from "./blueprintKeys";

const parsePieces = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);
/** What the spec stores for `text`: its first pieces up to the server's count, each cut
 *  to the server's length. Past either bound the save would 422 and autosave stop. */
const savedPieces = (text: string) =>
  parsePieces(text).slice(0, BLUEPRINT_LIMITS.piecesPerVariation).map((l) => l.slice(0, BLUEPRINT_LIMITS.piece));

/** Holds its own text so a trailing newline survives while you type; the spec only
    ever gets the non-empty lines. When `pieces` changes from outside (a draft's
    "Use it" swapping the spec under a same-named variation) the text follows it,
    but only when it differs from what the text already parses to. A line over the
    server's piece limit cannot be stopped by `maxLength` on a multi-line box, so it
    is warned about and cut to the limit in what is saved. */
function PiecesField({ name, pieces, onChange }: { name: string; pieces: string[]; onChange: (p: string[]) => void }) {
  const [text, setText] = useState(pieces.join("\n"));
  // The render-time adjust idiom: re-sync only when `pieces` itself changed and the text
  // no longer parses to it, so a keystroke (which also changes `pieces`, to the parse of
  // the text, cut to the limit) never rewrites what is being typed — including a line
  // still over the limit, which must stay as typed so its warning shows.
  const [seen, setSeen] = useState(pieces);
  if (pieces !== seen) {
    setSeen(pieces);
    const cur = savedPieces(text);
    if (cur.length !== pieces.length || cur.some((p, i) => p !== pieces[i])) setText(pieces.join("\n"));
  }
  const limit = BLUEPRINT_LIMITS.piece;
  const tooLong = text.split("\n").filter((l) => l.trim().length > limit).length;
  const maxPieces = BLUEPRINT_LIMITS.piecesPerVariation;
  const tooMany = Math.max(0, parsePieces(text).length - maxPieces);
  return (
    <>
      <textarea
        className="input" style={{ width: "100%", minHeight: 70, fontSize: 12, resize: "vertical" }}
        aria-label={`${name} pieces`} placeholder={"One piece per line\nlow angle\nwide shot"}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(savedPieces(e.target.value));
        }}
      />
      {tooLong > 0 && (
        <div role="alert" style={{ fontSize: 11, color: "var(--bad)", marginTop: 4 }}>
          {tooLong === 1 ? "One line is" : `${tooLong} lines are`} over {limit} characters. Only the
          first {limit} characters of {tooLong === 1 ? "it are" : "each are"} saved.
        </div>
      )}
      {tooMany > 0 && (
        <div role="alert" style={{ fontSize: 11, color: "var(--bad)", marginTop: 4 }}>
          A variation holds at most {maxPieces} pieces. The last {tooMany === 1 ? "line is" : `${tooMany} lines are`} not saved.
        </div>
      )}
    </>
  );
}

interface Props {
  variations: BlueprintSpec["variations"];
  onChange: (v: BlueprintSpec["variations"]) => void;
  /** Rename a variation and every template reference to it. */
  onRename: (from: string, to: string) => void;
  section: SectionControl;
  ref?: SectionRef;
}

export default function BlueprintVariationsEditor({ variations, onChange, onRename, section, ref }: Props) {
  const names = Object.keys(variations);
  const pieceCount = names.reduce((n, v) => n + variations[v].length, 0);
  const summary = `${names.length} variation${names.length === 1 ? "" : "s"} · ${pieceCount} piece${pieceCount === 1 ? "" : "s"}`;
  return (
    <BlueprintSection
      ref={ref} title="Variations" summary={summary} control={section}
      action={
        <button className="btn ghost sm" disabled={names.length >= BLUEPRINT_LIMITS.variations}
          title={names.length >= BLUEPRINT_LIMITS.variations ? `A blueprint holds at most ${BLUEPRINT_LIMITS.variations} variations` : undefined}
          onClick={() => { section.setOpen(true); onChange({ ...variations, [freshName(variations, "variation")]: [] }); }}>+ Add variation</button>
      }
    >
        {names.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: 0 }}>
            A variation is a list of phrases, such as camera angles. Each prompt picks one without
            changing the folder.
          </p>
        )}
        {names.map((name) => (
          <div key={name} data-testid={`variation-${name}`}
            style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
              <BlueprintNameField
                value={name} label="Variation name" placeholder="letters, digits, _"
                onCommit={(n) => {
                  if (n in variations) return false;
                  onRename(name, n);
                  return true;
                }}
              />
              <div style={{ flex: 1 }} />
              <button className="btn ghost sm" style={{ color: "var(--bad)" }} aria-label={`Remove variation ${name}`}
                onClick={() => {
                  const next = { ...variations };
                  delete next[name];
                  onChange(next);
                }}>Remove variation</button>
            </div>
            <PiecesField name={name} pieces={variations[name]} onChange={(p) => onChange({ ...variations, [name]: p })} />
          </div>
        ))}
    </BlueprintSection>
  );
}
