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
 * not be one: it is a filter, not an identity. `paneGo` writes both the pane view
 * and the URL, so pane state wins in split view and the query string covers the
 * routed case. "" is a real value here (the dataset root).
 */
export function usePaneGallerySubfolder(): string | undefined {
  const ctx = usePaneContext();
  const [params] = useSearchParams();
  return ctx?.view.subfolder ?? params.get("subfolder") ?? undefined;
}
