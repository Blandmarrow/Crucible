import { Scissors } from "lucide-react";
import type { ReactNode } from "react";
import { useModalBehavior } from "../../hooks/useModalBehavior";
import ReextractFramesForm from "./ReextractFramesForm";

interface Props {
  /** `ReextractFramesForm`'s props, verbatim — this only wraps it. */
  datasetId: string;
  imageIds?: string[];
  videoId?: string;
  subfolder?: string;
  onSuccess?: () => void;
  onClose: () => void;
  /** Visible heading, and the dialog's accessible name suffix. */
  title: string;
  /** A line under the heading: `SelectionToolbar`'s dataset breakdown,
   *  `VideoDetailPage`'s `{filename} · {subfolder}`. */
  headerExtra?: ReactNode;
}

/**
 * The re-extract dialog — the one place the three entry points share.
 *
 * A component rather than a `useModalBehavior` call in each page: the hook must
 * not be called conditionally, and every entry point renders its dialog behind a
 * flag. `useModalBehavior`'s own docstring rules out a generic wrapper (the app's
 * modals have heterogeneous markup), so a feature-specific one is the sanctioned
 * shape — this is `ExtractFramesModal`'s pattern, the sibling on the same page.
 *
 * Backdrop-click closing stays off (the hook's default) — a stray click on the
 * overlay should not dismiss a dialog with a run in flight. Escape and the ✕ do
 * close, at any time, because closing is now reversible: the form adopts live
 * `video_reextract` jobs for the preview's videos back into its tracked ids when
 * it reopens, and `TopBar` carries the invalidations either way.
 */
export default function ReextractFramesModal({
  datasetId, imageIds, videoId, subfolder, onSuccess, onClose, title, headerExtra,
}: Props) {
  const { overlayProps, panelProps } = useModalBehavior({
    onClose,
    label: "Re-extract at full resolution",
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" {...overlayProps}>
      <div
        className="card p-5 w-full max-w-md space-y-1 max-h-[80vh] overflow-y-auto"
        {...panelProps}
      >
        <h4 className="font-medium flex items-center gap-2 mb-1">
          <Scissors size={15} /> {title}
          <div style={{ flex: 1 }} />
          <button className="icon-btn" title="Close" onClick={onClose}>×</button>
        </h4>
        {headerExtra}
        <ReextractFramesForm
          datasetId={datasetId}
          imageIds={imageIds}
          videoId={videoId}
          subfolder={subfolder}
          onSuccess={onSuccess}
          onCancel={onClose}
        />
      </div>
    </div>
  );
}
