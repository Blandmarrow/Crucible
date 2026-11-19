import { usePaneNavigate } from "../../hooks/usePaneNavigate";
import type { Finding } from "../../api/health";
import type { PageType } from "../../contexts/PaneContext";
import { encodeFilterLink } from "../../utils/filterLink";

/** A finding's two navigating buttons, shared by the Health page and the chat. */
export function useFindingActions(datasetId: string) {
  const { go } = usePaneNavigate();

  // The finding's images in the gallery, where they can be selected and acted on
  // (the modal only previews them). The gallery applies the link once and clears
  // every other filter: see docs/dev/gallery/state.md.
  const openInGallery = (f: Finding) => {
    if (!f.filter_spec) return;
    const filter = encodeFilterLink({ label: f.title, spec: f.filter_spec });
    go(`/datasets/${datasetId}/gallery?filter=${filter}`, { page: "gallery", datasetId, filter });
  };

  const openFix = (f: Finding) => {
    if (!f.fix) return;
    if (f.fix.page === "datasets") go("/", { page: "datasets" });
    else go(`/datasets/${datasetId}/${f.fix.page}`, { page: f.fix.page as PageType, datasetId });
  };

  return { openInGallery, openFix };
}
