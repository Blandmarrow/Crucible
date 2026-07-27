import client from "./client";
import type { Video } from "../types";

export const videosApi = {
  list: (dataset_id: string): Promise<Video[]> =>
    client.get("/videos/", { params: { dataset_id } }).then((r) => r.data),

  get: (id: string): Promise<Video> => client.get(`/videos/${id}`).then((r) => r.data),

  /** Source URL for a <video> element. Served with range support, so seeking works. */
  fileUrl: (id: string) => `/api/v1/videos/${id}/file`,

  /** Cuts a poster on demand for a row that has none, so this is safe to point
   *  an <img> at even when `has_poster` is false — it only 404s for a video
   *  whose frames will not decode at all. */
  posterUrl: (id: string) => `/api/v1/videos/${id}/poster`,

  /** The poster URL is keyed by id alone, so a regenerated or renamed poster
   *  would serve stale from cache without this. Mirrors
   *  `imagesApi.thumbnailUrlVersioned`. */
  posterUrlVersioned: (id: string, updatedAt: string) =>
    `/api/v1/videos/${id}/poster?v=${Date.parse(updatedAt)}`,

  /** Stem only — the container extension is never user-settable, because the
   *  browser picks its decoder from it. */
  rename: (id: string, new_stem: string): Promise<{ filename: string }> =>
    client.patch(`/videos/${id}/rename`, { new_stem }).then((r) => r.data),

  delete: (id: string): Promise<void> => client.delete(`/videos/${id}`).then(() => undefined),
};
