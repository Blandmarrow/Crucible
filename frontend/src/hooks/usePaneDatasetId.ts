import { useParams } from "react-router";
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
