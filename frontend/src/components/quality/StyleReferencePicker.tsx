import { useInfiniteQuery } from "@tanstack/react-query";
import { Check, Upload, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { imagesApi } from "../../api/images";

interface Props {
  datasetId: string;
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  externalFiles: File[];
  onExternalFilesChange: (files: File[]) => void;
}

const PAGE_SIZE = 100;

export default function StyleReferencePicker({
  datasetId,
  selectedIds,
  onToggle,
  externalFiles,
  onExternalFilesChange,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [objectUrls, setObjectUrls] = useState<Map<string, string>>(new Map());

  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } =
    useInfiniteQuery({
      queryKey: ["images", datasetId, "picker"],
      queryFn: ({ pageParam }) =>
        imagesApi.list({
          dataset_id: datasetId,
          limit: PAGE_SIZE,
          page: pageParam as number,
          sort: "created_at",
          order: "desc",
        }),
      initialPageParam: 1,
      getNextPageParam: (lastPage, allPages) =>
        lastPage.length === PAGE_SIZE ? allPages.length + 1 : undefined,
    });

  const allImages = data?.pages.flat() ?? [];

  useEffect(() => {
    const newUrls = new Map<string, string>();
    externalFiles.forEach((f) => {
      newUrls.set(f.name + f.size, URL.createObjectURL(f));
    });
    setObjectUrls(newUrls);
    return () => {
      newUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [externalFiles]);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!e.target.files) return;
      onExternalFilesChange([...externalFiles, ...Array.from(e.target.files)]);
      e.target.value = "";
    },
    [externalFiles, onExternalFilesChange]
  );

  const removeExternalFile = (index: number) => {
    onExternalFilesChange(externalFiles.filter((_, i) => i !== index));
  };

  const totalSelected = selectedIds.size + externalFiles.length;

  return (
    <div className="space-y-3">
      {/* Dataset images */}
      <div>
        <p className="text-xs text-gray-500 mb-1.5">From dataset</p>
        {isLoading ? (
          <div className="text-xs text-gray-500 py-1">Loading images…</div>
        ) : allImages.length === 0 ? (
          <div className="text-xs text-gray-500 py-1">No images in dataset.</div>
        ) : (
          <>
            <div className="grid grid-cols-6 gap-1.5 max-h-48 overflow-y-auto pr-1">
              {allImages.map((img) => {
                const selected = selectedIds.has(img.id);
                return (
                  <button
                    key={img.id}
                    type="button"
                    title={img.filename}
                    onClick={() => onToggle(img.id)}
                    className={clsx(
                      "relative w-full aspect-square rounded overflow-hidden border-2 transition-all",
                      selected
                        ? "border-accent"
                        : "border-transparent hover:border-gray-500"
                    )}
                  >
                    <img
                      src={imagesApi.thumbnailUrlVersioned(img.id, img.updated_at)}
                      alt={img.filename}
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                    {selected && (
                      <div className="absolute inset-0 bg-accent/30 flex items-center justify-center">
                        <Check size={14} className="text-white drop-shadow" />
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
            {hasNextPage && (
              <button
                className="btn-secondary btn-sm mt-1.5 w-full text-center"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage
                  ? "Loading…"
                  : `Load more (${allImages.length} shown)`}
              </button>
            )}
          </>
        )}
      </div>

      {/* Local file references */}
      <div>
        <p className="text-xs text-gray-500 mb-1.5">
          From local files{" "}
          <span className="text-gray-600">— embedded on-the-fly, no pre-computation needed</span>
        </p>
        {externalFiles.length > 0 && (
          <div className="grid grid-cols-6 gap-1.5 mb-2">
            {externalFiles.map((f, i) => {
              const key = f.name + f.size;
              const url = objectUrls.get(key);
              return (
                <div
                  key={key}
                  className="relative w-full aspect-square rounded overflow-hidden border-2 border-accent"
                >
                  {url && (
                    <img
                      src={url}
                      alt={f.name}
                      title={f.name}
                      className="w-full h-full object-cover"
                    />
                  )}
                  <button
                    type="button"
                    className="absolute top-0 right-0 bg-black/70 rounded-bl p-0.5"
                    onClick={() => removeExternalFile(i)}
                    title="Remove"
                  >
                    <X size={10} className="text-white" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
        <button
          type="button"
          className="btn-secondary btn-sm flex items-center gap-1.5"
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload size={12} /> Browse local files
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*"
          className="hidden"
          onChange={handleFileChange}
        />
      </div>

      {/* Summary */}
      <p className="text-xs text-gray-500">
        {totalSelected > 0
          ? `${totalSelected} reference image${totalSelected !== 1 ? "s" : ""} selected` +
            (selectedIds.size > 0 && externalFiles.length > 0
              ? ` (${selectedIds.size} from dataset, ${externalFiles.length} local)`
              : "")
          : "Select references from dataset or browse local files"}
      </p>
    </div>
  );
}
