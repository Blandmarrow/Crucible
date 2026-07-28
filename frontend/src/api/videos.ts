import client from "./client";
import type {
  ExtractCapabilities,
  Video,
  VideoExtractRequest,
  VideoExtractResult,
  VideoFramesSummary,
  VideoProbeRequest,
  VideoProbeResult,
  VideoReextractRequest,
  VideoReextractResult,
} from "../types";

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

  /** What the extraction backend can do, independently of any one video. These
   *  also ride on the probe response, but a video that will not probe still
   *  extracts — so the modal reads this in preference and keeps its capability
   *  warnings working. Pure server-side; cache it hard. */
  capabilities: (): Promise<ExtractCapabilities> =>
    client.get("/videos/capabilities").then((r) => r.data),

  /** Sample a video for the extraction modal's first step. A plain request, not a
   *  job — twelve seeks finish before the modal has finished animating. Writes
   *  nothing but metadata correction, so it is safe to re-run on every trim drag. */
  probe: (id: string, body: VideoProbeRequest): Promise<VideoProbeResult> =>
    client.post(`/videos/${id}/probe`, body).then((r) => r.data),

  /** One job per video — `video_ids` is a batch, not a merge. Also the request
   *  that commits the confirmed crop/deinterlace/trims to the Video rows. */
  extract: (body: VideoExtractRequest): Promise<VideoExtractResult> =>
    client.post("/videos/extract", body).then((r) => r.data),

  /** Resolves a re-extraction without writing anything. Shares one resolver with
   *  `reextract`, so the accounting it reports is exactly what the jobs will do. */
  reextractPreview: (body: VideoReextractRequest): Promise<VideoReextractResult> =>
    client.post("/videos/reextract/preview", body).then((r) => r.data),

  /** Pass 2 — re-cut curated frames at full resolution, one job per video. The
   *  frames are rewritten *in place*: same row, same id, same filename unless the
   *  format changes the extension. */
  reextract: (body: VideoReextractRequest): Promise<VideoReextractResult> =>
    client.post("/videos/reextract", body).then((r) => r.data),

  /** Where this video's frames live, grouped by subfolder, newest first. Feeds
   *  the history panel, the delete-confirm count and the replace-mode label. */
  framesSummary: (id: string): Promise<VideoFramesSummary> =>
    client.get(`/videos/${id}/frames-summary`).then((r) => r.data),
};
