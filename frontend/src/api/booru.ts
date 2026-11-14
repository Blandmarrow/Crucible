import client from "./client";
import type { BooruTag, TagCheckResponse } from "../types";

export const booruApi = {
  search: (q: string, source: "safebooru" | "gelbooru" = "safebooru", limit = 20) =>
    client.get<BooruTag[]>("/booru/search", { params: { q, source, limit } }).then((r) => r.data),
  autocomplete: (prefix: string, source = "safebooru", limit = 10) =>
    client.post<BooruTag[]>("/booru/autocomplete", { prefix, source, limit }).then((r) => r.data),
  /** Check a caption's tags against Danbooru. Sends them off the machine: press-only. */
  check: (tags: string[]) =>
    client.post<TagCheckResponse>("/booru/check", { tags }).then((r) => r.data),
};
