import client from "./client";

/** The query keys of the two note scopes. */
export const datasetNotesKey = (datasetId: string) => ["dataset-notes", datasetId] as const;
export const APP_NOTES_KEY = ["app-notes"] as const;

export type DatasetNoteSection = "preference" | "decision" | "open_item";
export type AppNoteSection = "about_me";

export interface NoteItem {
  id: string;
  section: DatasetNoteSection | AppNoteSection;
  text: string;
  position: number;
  decided_at: string | null;
  /** Set on a dismissal: the health finding it hides. */
  finding_id: string | null;
  permanent: boolean;
  /** The chat whose accepted proposal added it; null when written by hand. */
  source_chat_id: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface Notes {
  items: NoteItem[];
  used_chars: number;
  cap_chars: number;
}

export interface NoteUpdate {
  text?: string;
  position?: number;
  /** null clears it; leave the key out to keep it. Decisions only. */
  decided_at?: string | null;
}

/** One scope's routes: a dataset's notes, or the app-wide "About me". */
function scopeApi(base: string) {
  return {
    list: (includeDeleted = false) =>
      client.get<Notes>(base, { params: { include_deleted: includeDeleted } }).then((r) => r.data),
    add: (body: { section: string; text: string; decided_at?: string | null }) =>
      client.post<NoteItem>(base, body).then((r) => r.data),
    update: (id: string, body: NoteUpdate) =>
      client.patch<NoteItem>(`${base}/${id}`, body).then((r) => r.data),
    remove: (id: string) => client.delete<NoteItem>(`${base}/${id}`).then((r) => r.data),
    restore: (id: string) => client.post<NoteItem>(`${base}/${id}/restore`).then((r) => r.data),
  };
}

export const notesApi = {
  dataset: (datasetId: string) => scopeApi(`/datasets/${datasetId}/notes`),
  app: () => scopeApi("/app-notes"),
};
