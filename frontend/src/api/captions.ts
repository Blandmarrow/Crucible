import client from "./client";
import type { CaptionData, TagStat } from "../types";

export const captionsApi = {
  get: (imageId: string) => client.get<CaptionData>(`/captions/image/${imageId}`).then((r) => r.data),
  update: (imageId: string, data: { caption_text: string; tags: string[]; caption_style?: string }) =>
    client.put<CaptionData>(`/captions/image/${imageId}`, data).then((r) => r.data),
  patchTags: (imageId: string, add: string[], remove: string[]) =>
    client.patch(`/captions/image/${imageId}/tags`, { add, remove }),
  batchSetTags: (image_ids: string[], tags: string[], mode: "append" | "replace" = "append") =>
    client.post("/captions/batch/set-tags", { image_ids, tags, mode }),
  batchRemoveTags: (image_ids: string[], tags: string[]) =>
    client.post("/captions/batch/remove-tags", { image_ids, tags }),
  tagStats: (dataset_id: string, subfolder?: string) =>
    client.get<TagStat[]>(`/captions/dataset/${dataset_id}/tag-stats`, { params: { subfolder } }).then((r) => r.data),
  findReplace: (
    dataset_id: string,
    find: string,
    replace: string,
    use_regex = false,
    image_ids?: string[],
  ) =>
    client
      .post<{ updated: number }>(`/captions/dataset/${dataset_id}/find-replace`, {
        find,
        replace,
        use_regex,
        image_ids,
      })
      .then((r) => r.data),
};
