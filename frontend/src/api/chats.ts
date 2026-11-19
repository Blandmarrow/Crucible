import client from "./client";
import { apiErrorDetail } from "../utils/apiError";

/** What the analyst's context can carry, each behind its own toggle. `images` is
 *  reserved for a vision phase: the server refuses to turn it on. Mirrors
 *  `backend/schemas/chat.py::PRIVACY_CATEGORIES`. */
export const PRIVACY_CATEGORIES = ["numbers", "identity", "notes", "tags", "captions", "file_names", "images"] as const;
export type PrivacyCategory = (typeof PRIVACY_CATEGORIES)[number];
export type Privacy = Partial<Record<PrivacyCategory, boolean>>;

export const PRIVACY_LABELS: Record<PrivacyCategory, { label: string; help: string }> = {
  numbers: { label: "Numbers", help: "Counts, distributions and the findings" },
  identity: { label: "Name and goal", help: "The dataset's name and what it is for" },
  notes: { label: "Notes", help: "This dataset's notes and About me" },
  tags: { label: "Tags", help: "The most common tags in tag-style captions" },
  captions: { label: "Captions", help: "A sample of up to 20 captions (this also reveals tags)" },
  file_names: { label: "File names", help: "The sample's file names, never a folder path" },
  images: { label: "Images", help: "Not sent yet: reserved for a later version" },
};

export interface Chat {
  id: string;
  dataset_id: string;
  title: string;
  provider_id: string | null;
  model: string;
  /** The chat's own override; null follows the provider. */
  privacy: Privacy | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export type MessageStatus = "complete" | "partial" | "cancelled" | "error";

export interface CategoryManifest {
  sent: boolean;
  chars: number;
  items: number;
}

export interface ContextManifest {
  categories: Record<string, CategoryManifest>;
  privacy_source: "chat" | "provider" | "none";
  history_messages: number;
  history_dropped: number;
  history_withheld: number;
  total_chars: number;
  estimated_tokens: number;
  note_numbers: Record<string, string[]>;
}

/** One change to the notes a reply proposed (docs/dev/analyst/proposals.md). */
export interface Proposal {
  id: number;
  seq: number;
  op: "add" | "edit" | "remove";
  section: "preference" | "decision" | "open_item" | "about_me";
  /** Edit and remove: the note the reply's numbering named, and its text then. */
  target_item_id: string | null;
  target_text: string | null;
  /** Add and edit: the proposed text. */
  new_text: string | null;
  status: "pending" | "accepted" | "rejected" | "stale";
  applied_item_id: string | null;
}

export const NOTE_SECTION_LABELS: Record<Proposal["section"], string> = {
  preference: "Preferences",
  decision: "Decisions",
  open_item: "Open items",
  about_me: "About me",
};

/** A reply's `crucible-notes` blocks, closed or not, as
 *  `backend/services/note_proposals.py::BLOCK_RE`: hidden from the displayed text,
 *  which shows the parsed proposals instead. A closing fence counts only at the start
 *  of a line; the end of the text closes an unclosed block. */
const NOTES_BLOCK = /```[ \t]*crucible-notes[ \t]*\r?\n[\s\S]*?(?:^[ \t]*```|(?![\s\S]))/gim;
/** A fence line still arriving at the end of a reply being written, such as
 *  "```crucible-no": hidden until its newline shows what it opens. */
const PARTIAL_FENCE = /(^|\n)[ \t]*```[^\n]*$/;
export function stripNoteBlocks(text: string, writing = false): string {
  const shown = writing ? text.replace(PARTIAL_FENCE, "$1") : text;
  return shown.replace(NOTES_BLOCK, "").replace(/\n{3,}/g, "\n\n").trim();
}

/** What "Ask it to note this" sends: a narrow follow-up for a model that agreed to
 *  something but wrote no block. */
export const ASK_FOR_NOTES =
  "Please write the crucible-notes block for what we agreed, and nothing else.";

export interface ChatMessage {
  id: number;
  seq: number;
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  error_kind: string | null;
  error_detail: string | null;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tokens_estimated: boolean;
  context_manifest: ContextManifest | null;
  /** Finding ids for the strip under a reply, chosen by Crucible. */
  related_findings: string[] | null;
  /** The reply's note proposals; empty on a user message. */
  proposals: Proposal[];
  created_at: string;
}

export interface ChatDetail extends Chat {
  messages: ChatMessage[];
}

export interface Budget {
  billed: boolean;
  /** The most the next send could use: its context plus a full-length reply. */
  estimate: number;
  chat_used: number;
  chat_budget: number;
  day_used: number;
  day_budget: number;
  would_exceed: boolean;
}

export interface ContextPreview {
  messages: { role: "system" | "user" | "assistant"; content: string }[];
  manifest: ContextManifest;
  privacy: Record<PrivacyCategory, boolean>;
  budget: Budget;
}

export interface ChatUpdate {
  title?: string;
  provider_id?: string | null;
  model?: string;
  /** null clears the override. Never sent with `provider_id`: the server refuses that. */
  privacy?: Privacy | null;
}

export const chatsKey = (datasetId: string) => ["chats", datasetId] as const;
export const chatKey = (chatId: string) => ["chat", chatId] as const;
export const chatPreviewKey = (chatId: string) => ["chat-preview", chatId] as const;

export const chatsApi = {
  list: (datasetId: string) => client.get<Chat[]>(`/datasets/${datasetId}/chats`).then((r) => r.data),
  create: (datasetId: string, body: { title?: string; provider_id?: string | null; model?: string }) =>
    client.post<Chat>(`/datasets/${datasetId}/chats`, body).then((r) => r.data),
  get: (chatId: string) => client.get<ChatDetail>(`/chats/${chatId}`).then((r) => r.data),
  update: (chatId: string, body: ChatUpdate) => client.patch<Chat>(`/chats/${chatId}`, body).then((r) => r.data),
  remove: (chatId: string) => client.delete(`/chats/${chatId}`),
  preview: (chatId: string, draft: string, signal?: AbortSignal) =>
    client.post<ContextPreview>(`/chats/${chatId}/preview-context`, { draft }, { signal }).then((r) => r.data),
  actOnProposal: (chatId: string, proposalId: number, action: "accept" | "reject" | "undo") =>
    client.post<Proposal>(`/chats/${chatId}/proposals/${proposalId}/${action}`).then((r) => r.data),
  cancel: (chatId: string, messageId: number) =>
    client.post<ChatMessage>(`/chats/${chatId}/messages/${messageId}/cancel`).then((r) => r.data),
};

/** One server-sent event from a reply's stream (`docs/dev/analyst/sending.md` § Events). */
export type ReplyEvent =
  | { event: "start"; data: { chat_id: string; message_id: number; user_message_id: number | null; content: string } }
  | { event: "delta"; data: { text: string } }
  | { event: "usage"; data: { tokens_in: number; tokens_out: number; estimated: boolean } }
  | { event: "done" | "error"; data: ChatMessage };

/** A refusal before the stream opened (no provider, a reply already running, a
 *  budget): the server's own reason, read as `apiErrorDetail` reads an axios error. */
export class SendRefused extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Open a reply's event stream: `POST …/messages` to send, or `GET …/stream` to pick a
 *  running reply back up. `EventSource` cannot send a body, so this is `fetch` and a
 *  stream reader. Resolves once the stream ends; a refusal throws `SendRefused`. */
export async function openReplyStream(
  url: string,
  init: { method: "POST" | "GET"; body?: unknown; signal?: AbortSignal },
  onEvent: (e: ReplyEvent) => void,
  /** The server accepted the request and the stream is open. */
  onOpen?: () => void,
): Promise<void> {
  const res = await fetch(`/api/v1${url}`, {
    method: init.method,
    headers: init.body === undefined ? { Accept: "text/event-stream" } : { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    signal: init.signal,
  });
  if (!res.ok || !res.body) {
    let data: unknown = null;
    try { data = await res.json(); } catch { /* not JSON: fall back to the status */ }
    // The same parsing as an axios error, so a 422's field list reads the same way.
    const message = apiErrorDetail({ response: { data } }, `The request failed (${res.status})`);
    throw new SendRefused(res.status, message);
  }

  onOpen?.();
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    // Events end with a blank line; sse-starlette writes CRLF.
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const parsed = parseEvent(block);
      if (parsed) onEvent(parsed);
    }
  }
  const tail = parseEvent(buffer);
  if (tail) onEvent(tail);
}

function parseEvent(block: string): ReplyEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue; // a keep-alive comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) } as ReplyEvent;
  } catch {
    return null;
  }
}
