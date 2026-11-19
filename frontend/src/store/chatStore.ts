import { create } from "zustand";
import type { QueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  chatKey,
  chatPreviewKey,
  chatsApi,
  chatsKey,
  openReplyStream,
  SendRefused,
  type ChatMessage,
  type ReplyEvent,
} from "../api/chats";
import { apiErrorDetail } from "../utils/apiError";

/** A reply being written, as the browser sees it. Lives here rather than in a
 *  component, so the stream survives a pane remount, a pane move or leaving the page
 *  (`docs/dev/analyst/chat-ui.md` § The stream lives outside React). */
export interface ChatStream {
  chatId: string;
  datasetId: string;
  /** The user's message, shown until the stored copy arrives. Null on a resume. */
  userText: string | null;
  userMessageId: number | null;
  /** The assistant row, known once `start` arrives. */
  messageId: number | null;
  text: string;
  phase: "connecting" | "streaming" | "ended";
  stopping: boolean;
  usage: { tokens_in: number; tokens_out: number; estimated: boolean } | null;
}

interface ChatStore {
  /** By chat id; at most one reply runs per chat (the server's 409 says so too). */
  streams: Record<string, ChatStream>;
  /** The chat each dataset last showed, so a pane coming back to it reopens it. */
  openChat: Record<string, string>;
  setOpenChat: (datasetId: string, chatId: string | null) => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  streams: {},
  openChat: {},
  setOpenChat: (datasetId, chatId) =>
    set((s) => {
      const openChat = { ...s.openChat };
      if (chatId) openChat[datasetId] = chatId;
      else delete openChat[datasetId];
      return { openChat };
    }),
}));

// The fetches themselves: outside the store's state, since an AbortController is not
// data a component renders. Keyed by chat id, like `streams`.
const controllers = new Map<string, AbortController>();
const resumed = new Set<number>();

function patch(chatId: string, change: Partial<ChatStream>) {
  useChatStore.setState((s) => {
    const cur = s.streams[chatId];
    if (!cur) return s;
    return { streams: { ...s.streams, [chatId]: { ...cur, ...change } } };
  });
}

function drop(chatId: string) {
  controllers.delete(chatId);
  useChatStore.setState((s) => {
    if (!(chatId in s.streams)) return s;
    const streams = { ...s.streams };
    delete streams[chatId];
    return { streams };
  });
}

function onEvent(chatId: string, e: ReplyEvent) {
  const cur = useChatStore.getState().streams[chatId];
  if (!cur) return;
  switch (e.event) {
    case "start":
      patch(chatId, {
        messageId: e.data.message_id,
        userMessageId: e.data.user_message_id ?? cur.userMessageId,
        text: e.data.content,
        phase: "streaming",
      });
      break;
    case "delta":
      patch(chatId, { text: cur.text + e.data.text });
      break;
    case "usage":
      patch(chatId, { usage: e.data });
      break;
    case "done":
    case "error":
      patch(chatId, { text: e.data.content, phase: "ended" });
      break;
  }
}

/** The stored chat, then the stream entry dropped: in that order, so the reply never
 *  blinks out between the streamed copy and the stored one. */
async function settle(qc: QueryClient, s: ChatStream) {
  try {
    await Promise.all([
      qc.invalidateQueries({ queryKey: chatKey(s.chatId) }),
      qc.invalidateQueries({ queryKey: chatsKey(s.datasetId) }),
    ]);
  } finally {
    // The budget figures moved with the call, whatever it cost.
    qc.invalidateQueries({ queryKey: chatPreviewKey(s.chatId) });
    drop(s.chatId);
  }
}

/** Open the stream and read it to the end in the background. The promise returned
 *  resolves as soon as the outcome of the request itself is known: true once the
 *  server accepted it, false when it refused or could not be reached (nothing stored). */
function run(qc: QueryClient, stream: ChatStream, url: string, method: "POST" | "GET", body?: unknown): Promise<boolean> {
  const ac = new AbortController();
  controllers.set(stream.chatId, ac);
  useChatStore.setState((s) => ({ streams: { ...s.streams, [stream.chatId]: stream } }));
  let opened = false;
  let accept: (ok: boolean) => void = () => {};
  const accepted = new Promise<boolean>((resolve) => { accept = resolve; });

  void (async () => {
    try {
      await openReplyStream(
        url,
        { method, body, signal: ac.signal },
        (e) => onEvent(stream.chatId, e),
        () => { opened = true; accept(true); },
      );
    } catch (err) {
      if (ac.signal.aborted) {
        // Our own abort (the chat is being deleted): nothing to report.
      } else if (err instanceof SendRefused) {
        toast.error(err.message);
      } else if (opened) {
        // The connection dropped mid-reply. The server keeps writing it, so the stored
        // row (refetched below) shows what arrived, and reopening the chat resumes it.
        toast.error("Lost the connection to the reply. Reopen the chat to pick it back up.");
      } else {
        toast.error(apiErrorDetail(err, "Could not reach Crucible's server."));
      }
    } finally {
      accept(opened);
      const last = useChatStore.getState().streams[stream.chatId] ?? stream;
      await settle(qc, last);
    }
  })();
  return accepted;
}

/** Send a message and stream the reply. Resolves false when the server refused the
 *  send (nothing was stored), so the composer can keep the draft. */
export function sendMessage(qc: QueryClient, datasetId: string, chatId: string, content: string): Promise<boolean> {
  if (useChatStore.getState().streams[chatId]) return Promise.resolve(false);
  return run(qc, {
    chatId, datasetId, userText: content, userMessageId: null, messageId: null,
    text: "", phase: "connecting", stopping: false, usage: null,
  }, `/chats/${chatId}/messages`, "POST", { content });
}

/** Pick a reply that is still being written back up, after a reload or on reopening a
 *  chat. A reply that ended meanwhile answers with its final event at once. */
export function resumeReply(qc: QueryClient, datasetId: string, chatId: string, messageId: number) {
  // Once per reply per page load. A row left `partial` with no run behind it (the
  // startup sweep has not seen it yet) would otherwise resume, refetch as `partial`
  // and resume again, forever.
  if (useChatStore.getState().streams[chatId] || resumed.has(messageId)) return;
  resumed.add(messageId);
  void run(qc, {
    chatId, datasetId, userText: null, userMessageId: null, messageId,
    text: "", phase: "connecting", stopping: false, usage: null,
  }, `/chats/${chatId}/messages/${messageId}/stream`, "GET");
}

/** Stop a reply, keeping what arrived. The stream then ends with the stored message. */
export async function stopReply(chatId: string, messageId: number): Promise<ChatMessage | null> {
  patch(chatId, { stopping: true });
  try {
    return await chatsApi.cancel(chatId, messageId);
  } catch (err) {
    patch(chatId, { stopping: false });
    toast.error(apiErrorDetail(err, "Could not stop the reply"));
    return null;
  }
}

/** Stop listening without stopping the reply: for a chat being deleted, whose stream
 *  the server ends itself. */
export function forgetStream(chatId: string) {
  controllers.get(chatId)?.abort();
  drop(chatId);
}
