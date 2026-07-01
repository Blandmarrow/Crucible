import client from "./client";

export interface FsEntry {
  name: string;
  path: string;
  type: "dir" | "file";
  size_bytes: number | null;
  modified_at: string;
  is_image: boolean;
  extension: string | null;
}

export interface FsImageMeta {
  width: number | null;
  height: number | null;
  format: string | null;
  file_size_bytes: number | null;
  generation_metadata: Record<string, unknown> | null;
}

export const filesystemApi = {
  async roots(): Promise<{ roots: { path: string; label: string }[]; datasets_dir: string }> {
    const r = await client.get("/filesystem/roots");
    return r.data;
  },

  async list(path: string): Promise<{ path: string; entries: FsEntry[] }> {
    const r = await client.get("/filesystem/list", { params: { path } });
    return r.data;
  },

  previewUrl(path: string): string {
    return `/api/v1/filesystem/preview?path=${encodeURIComponent(path)}`;
  },

  async imageMeta(path: string): Promise<FsImageMeta> {
    const r = await client.get("/filesystem/image-meta", { params: { path } });
    return r.data;
  },

  async move(src: string, dst_dir: string): Promise<{ new_path: string }> {
    const r = await client.post("/filesystem/move", { src, dst_dir });
    return r.data;
  },

  async rename(path: string, new_name: string): Promise<{ new_path: string }> {
    const r = await client.post("/filesystem/rename", { path, new_name });
    return r.data;
  },

  async delete(path: string): Promise<void> {
    await client.post("/filesystem/delete", { path });
  },

  async mkdir(parent: string, name: string): Promise<{ path: string }> {
    const r = await client.post("/filesystem/mkdir", { parent, name });
    return r.data;
  },
};
