/** The gallery → image-detail navigation context: what the detail view's ← / →
 *  are stepping through.
 *
 *  `GalleryPage` writes it on every rendered page; `ImageDetailPage` reads it to
 *  find the current image's neighbours, and — at either end of the page — to
 *  prefetch the adjacent page so the arrows keep going. That prefetch is a
 *  *second* listing query, so it has to be issued with the same filters the grid
 *  used or it steps into a different result set: paging past the last image of a
 *  subfolder used to land in the middle of the whole dataset.
 *
 *  The shape used to be declared by three independent inline casts, and the
 *  writer dropped fields the reader needed with nothing to catch it. This module
 *  is the single declaration, so `filters` cannot be half-written again.
 *
 *  ## Rules
 *
 *  - **`filters` is the raw `ImageFilterParams` memo**, stored unserialized.
 *    Every field is `string | boolean | undefined`, so the JSON round-trip is
 *    lossless apart from `undefined` keys vanishing — which is exactly the
 *    wanted semantics (absent = no filter). `score_filters` is already a JSON
 *    *string*; do not stringify it a second time.
 *  - **Never normalize `subfolder` with `value || undefined`.** `""` is the
 *    dataset root and a real filter backend-side (`docs/dev/image-filters.md`),
 *    as is `captioned: false`. Collapsing either to `undefined` widens the
 *    prefetch to the whole dataset — the exact bug this module exists to stop.
 *  - The stored object has **fewer own keys** than the live memo (the
 *    `undefined` ones are gone), so a shallow "did the filters change?" compare
 *    between the two would always say yes.
 */
import type { ImageFilterParams, ImageIdsParams, ImageListParams } from "../api/images";
import { SORT_OPTIONS } from "../constants/galleryOptions";
import { getGalleryPageSize } from "../constants/storage";

/** Normalized and total — everything the detail view needs to reconstruct the
 *  grid's query for the adjacent page. Deliberately carries no `captionedFilter`
 *  field: that lives in `filters.captioned`, and keeping both would re-create the
 *  two sources of truth this module removes. */
export interface GalleryNavContext {
  ids: string[];
  page: number;
  limit: number;
  sort: string;
  order: string;
  filters: ImageFilterParams;
}

/** What may actually sit in sessionStorage — including v1 blobs written by a
 *  build that carried only the caption filter. The only place the legacy shape is
 *  named. */
interface StoredNavContext {
  ids?: unknown;
  page?: unknown;
  limit?: unknown;
  sort?: unknown;
  order?: unknown;
  filters?: unknown;
  /** v1 only. */
  captionedFilter?: boolean | null;
}

function navKey(datasetId: string): string {
  return `gallery-nav-${datasetId}`;
}

/** Read and normalize. A v1 blob (no `filters`) becomes
 *  `filters: { captioned: captionedFilter ?? undefined }` — guarded on the field
 *  being an object rather than on its absence, so a `null` or a string cannot
 *  reach the query as a filter set. */
export function readNavContext(datasetId: string): GalleryNavContext | null {
  try {
    const raw = sessionStorage.getItem(navKey(datasetId));
    if (!raw) return null;
    const stored = JSON.parse(raw) as StoredNavContext;
    if (!Array.isArray(stored.ids)) return null;
    const filters =
      typeof stored.filters === "object" && stored.filters !== null
        ? (stored.filters as ImageFilterParams)
        : ({ captioned: stored.captionedFilter ?? undefined } as ImageFilterParams);
    return {
      ids: stored.ids as string[],
      page: typeof stored.page === "number" ? stored.page : 1,
      limit: typeof stored.limit === "number" ? stored.limit : getGalleryPageSize(),
      // Fallbacks are `SORT_OPTIONS[0]`, not the gallery's default: that is
      // `getGalleryDefaultSort()`, a user setting. A blob written by the gallery
      // always carries both fields, so this branch is reachable only from a
      // hand-edited one — the first entry is the stable choice there.
      sort: typeof stored.sort === "string" ? stored.sort : SORT_OPTIONS[0].sort,
      order: typeof stored.order === "string" ? stored.order : SORT_OPTIONS[0].order,
      filters,
    };
  } catch {
    return null;
  }
}

/** Takes only the normalized shape, so a v1 blob can never be written again. */
export function writeNavContext(datasetId: string, ctx: GalleryNavContext): void {
  try {
    sessionStorage.setItem(navKey(datasetId), JSON.stringify(ctx));
  } catch { /* ignore */ }
}

/** The listing request for one page of a stored context. The three call sites — the
 *  two boundary prefetches and the post-delete refresh — must send byte-identical
 *  params or they describe different result sets. `dataset_id` sits *after* the
 *  spread: the stored blob carries whatever dataset was current when the gallery
 *  wrote it, and the URL wins. */
export function navPageParams(
  datasetId: string,
  ctx: GalleryNavContext,
  page: number,
): ImageListParams {
  return {
    ...ctx.filters,
    dataset_id: datasetId,
    page,
    limit: ctx.limit,
    sort: ctx.sort,
    order: ctx.order,
  };
}

/** The *whole* matching list of a stored context, in the order the arrows walk it —
 *  `GET /images/ids` rather than one page. `navPageParams`' sibling, and declared
 *  here for the same reason: the shape was once written by three independent inline
 *  casts, and the detail view's relocate fallback (find which page the open image
 *  slid onto) must not become a fourth. Carries `sort`/`order` but no `page`/`limit`
 *  — the endpoint returns every id, capped server-side. */
export function navIdsParams(datasetId: string, ctx: GalleryNavContext): ImageIdsParams {
  return {
    ...ctx.filters,
    dataset_id: datasetId,
    sort: ctx.sort,
    order: ctx.order,
  };
}

/** Rewrite just the id list, leaving the page/filters describing it alone.
 *  Routed through read/write, so a legacy blob is normalized on the way through
 *  rather than staying legacy. */
function mutateNavIds(datasetId: string, transform: (ids: string[]) => string[]) {
  const ctx = readNavContext(datasetId);
  if (!ctx) return;
  writeNavContext(datasetId, { ...ctx, ids: transform(ctx.ids) });
}

/** A derivative created in place (crop / upscale / LUT) takes the slot right
 *  after its parent, so ← / → walk onto it next. */
export function injectNavId(datasetId: string, afterId: string, newId: string) {
  mutateNavIds(datasetId, (ids) => {
    const next = [...ids];
    const idx = ids.indexOf(afterId);
    if (idx >= 0) next.splice(idx + 1, 0, newId);
    else next.push(newId);
    return next;
  });
}

export function removeNavId(datasetId: string, removedId: string) {
  mutateNavIds(datasetId, (ids) => ids.filter((id) => id !== removedId));
}
