import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { recipesApi, type RunRecipe, type SavedRecipe } from "../../api/runs";
import ConfirmDialog from "../common/ConfirmDialog";
import { apiErrorDetail } from "../../utils/apiError";
import { safeFilename, triggerDownload } from "../../utils/download";

const RECIPES_KEY = ["run-recipes"] as const;

function status(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

/**
 * The recipe library on the start form (M6 §6): load a saved recipe into the form,
 * save the form as one, rename, delete, and export or import one as a JSON file.
 *
 * Recipes are global, not per dataset. A loaded recipe keeps the form's plan and run
 * name; what it names that this machine lacks (a provider, an export folder) comes
 * back as notes from `onLoad`, which the page shows.
 */
export default function RecipeBar({ buildRecipe, onLoad }: {
  buildRecipe: () => RunRecipe;
  onLoad: (recipe: SavedRecipe) => void;
}) {
  const qc = useQueryClient();
  const { data: recipes = [] } = useQuery({ queryKey: RECIPES_KEY, queryFn: recipesApi.list });
  const [selectedId, setSelectedId] = useState("");
  const [saveName, setSaveName] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "replace" | "delete"; name: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const selected = recipes.find((r) => r.id === selectedId) ?? null;
  const refresh = () => qc.invalidateQueries({ queryKey: RECIPES_KEY });

  const save = useMutation({
    mutationFn: ({ name, replace }: { name: string; replace: boolean }) => recipesApi.save(name, buildRecipe(), replace),
    onSuccess: (r) => {
      toast.success(`Saved the recipe "${r.name}"`);
      setSelectedId(r.id);
      setSaveName("");
      refresh();
    },
    onError: (err, vars) => {
      if (status(err) === 409 && !vars.replace) setConfirm({ kind: "replace", name: vars.name });
      else toast.error(apiErrorDetail(err, "Could not save the recipe"));
    },
  });

  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => recipesApi.rename(id, name),
    onSuccess: () => { setRenaming(null); refresh(); },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not rename the recipe")),
  });

  const remove = useMutation({
    mutationFn: (id: string) => recipesApi.remove(id),
    onSuccess: () => { setSelectedId(""); refresh(); },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not delete the recipe")),
  });

  const exportIt = useMutation({
    mutationFn: (r: SavedRecipe) => recipesApi.exportDoc(r.id),
    onSuccess: (doc, r) =>
      triggerDownload(JSON.stringify(doc, null, 2), `${safeFilename(r.name) || "recipe"}.crucible-recipe.json`, "application/json"),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not export the recipe")),
  });

  const importIt = useMutation({
    mutationFn: (doc: unknown) => recipesApi.importDoc(doc),
    onSuccess: ({ recipe, notes }) => {
      toast.success(`Imported the recipe "${recipe.name}"`);
      for (const n of notes) toast(n, { duration: 8000 });
      setSelectedId(recipe.id);
      refresh();
      onLoad(recipe);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not import the recipe")),
  });

  const onFile = (file: File) => {
    file.text().then((text) => {
      let doc: unknown;
      try {
        doc = JSON.parse(text);
      } catch {
        toast.error("That file is not JSON");
        return;
      }
      importIt.mutate(doc);
    });
  };

  const busy = save.isPending || rename.isPending || remove.isPending || importIt.isPending;
  return (
    <div className="form-row">
      <div className="lbl-col"><h4>Recipe</h4><p>Saved run settings, shared by every dataset. Load one to fill the form, or save the form as one.</p></div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="row-flex" style={{ gap: 8, flexWrap: "wrap" }}>
          {renaming !== null && selected ? (
            <>
              <input className="input" aria-label="New recipe name" value={renaming} maxLength={120} autoFocus
                onChange={(e) => setRenaming(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && renaming.trim()) rename.mutate({ id: selected.id, name: renaming });
                  if (e.key === "Escape") { e.stopPropagation(); setRenaming(null); }
                }} />
              <button className="btn sm primary" disabled={!renaming.trim() || busy}
                onClick={() => rename.mutate({ id: selected.id, name: renaming })}>Rename</button>
              <button className="btn sm ghost" onClick={() => setRenaming(null)}>Cancel</button>
            </>
          ) : (
            <>
              <select className="select" aria-label="Saved recipes" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>
                <option value="">{recipes.length ? "Choose a saved recipe…" : "No saved recipes yet"}</option>
                {recipes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              <button className="btn sm" disabled={!selected || busy} onClick={() => selected && onLoad(selected)}>Load</button>
              <button className="btn sm ghost" disabled={!selected || busy} onClick={() => selected && setRenaming(selected.name)}>Rename</button>
              <button className="btn sm ghost" disabled={!selected || exportIt.isPending} onClick={() => selected && exportIt.mutate(selected)}>Export</button>
              <button className="btn sm ghost" style={{ color: selected ? "var(--bad)" : undefined }} disabled={!selected || busy}
                onClick={() => selected && setConfirm({ kind: "delete", name: selected.name })}>Delete</button>
            </>
          )}
        </div>
        <div className="row-flex" style={{ gap: 8, flexWrap: "wrap" }}>
          <input className="input" placeholder="Recipe name" aria-label="Recipe name" value={saveName} maxLength={120}
            onChange={(e) => setSaveName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && saveName.trim()) save.mutate({ name: saveName, replace: false }); }} />
          <button className="btn sm" disabled={!saveName.trim() || busy} onClick={() => save.mutate({ name: saveName, replace: false })}>
            Save as recipe
          </button>
          <button className="btn sm ghost" disabled={busy} onClick={() => fileRef.current?.click()}>Import…</button>
          <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: "none" }} data-testid="recipe-import-input"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onFile(f);
              e.target.value = "";
            }} />
        </div>
      </div>
      {confirm?.kind === "replace" && (
        <ConfirmDialog
          title="Replace the saved recipe?"
          message={`A recipe named "${confirm.name}" already exists. Replace its settings with this form's?`}
          confirmLabel="Replace"
          onConfirm={() => { const name = confirm.name; setConfirm(null); save.mutate({ name, replace: true }); }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm?.kind === "delete" && selected && (
        <ConfirmDialog
          title="Delete this recipe?"
          message={`"${selected.name}" is removed from the library. Runs already started with it are not changed.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => { setConfirm(null); remove.mutate(selected.id); }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}
