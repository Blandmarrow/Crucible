import client from "./client";
import type { Video } from "../types";

export const videosApi = {
  list: (dataset_id: string): Promise<Video[]> =>
    client.get("/videos/", { params: { dataset_id } }).then((r) => r.data),

  get: (id: string): Promise<Video> => client.get(`/videos/${id}`).then((r) => r.data),

  /** Source URL for a <video> element. Served with range support, so seeking works. */
  fileUrl: (id: string) => `/api/v1/videos/${id}/file`,

  /** 404s until a poster frame has been generated — check `has_poster` first. */
  posterUrl: (id: string) => `/api/v1/videos/${id}/poster`,

  delete: (id: string): Promise<void> => client.delete(`/videos/${id}`).then(() => undefined),
};
