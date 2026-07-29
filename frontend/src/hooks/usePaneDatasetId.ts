import { useParams, useSearchParams } from "react-router";
import { usePaneContext } from "../contexts/PaneContext";

export function usePaneDatasetId(): string | undefined {
  const ctx = usePaneContext();
  const { datasetId } = useParams<{ datasetId: string }>();
  return ctx?.view.datasetId ?? datasetId;
}

export function usePaneImageId(): string | undefined {
  const ctx = usePaneContext();
  const { imageId } = useParams<{ imageId: string }>();
  return ctx?.view.imageId ?? imageId;
}

export function usePaneVideoId(): string | undefined {
  const ctx = usePaneContext();
  const { videoId } = useParams<{ videoId: string }>();
  return ctx?.view.videoId ?? videoId;
}

/**
 * The subfolder a gallery link asked to open at, or undefined for "wherever the
 * page was last left".
 *
 * Written exactly like `usePaneVideoId`, except the fallback is a query string
 * rather than a route param — there is no `:subfolder` segment, and there should
 * not be one: it is a filter, not an identity. Inside a pane the view is the whole
 * answer and the query string is not consulted: `paneGo` only sets the view there,
 * so the URL still carries whatever was routed before split view opened — reading
 * it would apply one pane's deep link to the other, resetting its page. The query
 * string is the carrier in the routed case only. "" is a real value here (the
 * dataset root).
 */
export function usePaneGallerySubfolder(): string | undefined {
  const ctx = usePaneContext();
  const [params] = useSearchParams();
  if (ctx) return ctx.view.subfolder;
  return params.get("subfolder") ?? undefined;
}

/**
 * The video whose frames a gallery link asked to show, or undefined for "no
 * lineage filter asked for".
 *
 * The sibling of `usePaneGallerySubfolder`, and for the same reason: `routeToView`
 * parses the pathname only, so the query-string fallback is what carries the link
 * in the routed (non-split) case — and, for the same reason as its sibling, is not
 * consulted at all inside a pane.
 */
export function usePaneGallerySourceVideo(): string | undefined {
  const ctx = usePaneContext();
  const [params] = useSearchParams();
  if (ctx) return ctx.view.sourceVideoId;
  return params.get("source_video_id") ?? undefined;
}
