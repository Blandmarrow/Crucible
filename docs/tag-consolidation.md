# Tag Consolidation

Reduce redundant and synonymous wording across a dataset's captions.

Available from: the **Consolidate Tags** sidebar item on any dataset page.

It works on comma-separated segments of each caption — individual tags for booru-style captions, or whole phrases/sentences for natural-language captions (so the synonym finder can also consolidate near-duplicate phrasings in prose, not just booru tags). It offers two tools:

- **Quick cleanup** — deterministic, instant, no model. Removes redundant text *within* each caption: drops any tag/phrase that is wholly contained in a longer one in the same caption (`tail` when `long tail` is present; `shirt` when `white shirt` is present) and collapses exact duplicates. Whole-word matching means `car` is never removed because of `scar` or `carpet`. A live preview shows how many captions would change before you run it. The same cleanup is available across a gallery selection (selection toolbar → **Merge tags**) and on a single image (detail view → **Merge redundant tags**).
- **Find synonyms** — semantic clustering. Embeds the dataset's unique segments with a small text model (`all-MiniLM-L6-v2`, ~90 MB, downloaded on first use — a sentence transformer, so it handles whole phrases and sentences, not just short tags) and groups those whose meaning is close (e.g. `car` / `automobile` / `auto`) above a tunable similarity threshold. Each cluster proposes a canonical form (the most descriptive one); you review the proposals — search, sort (by impact, cluster size, "needs review", or name), edit which one wins, exclude individual variants, or skip whole clusters — then **Apply** rewrites every caption across the dataset. Replacement is whole-segment (so `car` never rewrites `carpet`), duplicates are collapsed, and the list is virtualized so it stays responsive even with hundreds of clusters. By default every cluster is accepted, so a clean run is essentially one click.
