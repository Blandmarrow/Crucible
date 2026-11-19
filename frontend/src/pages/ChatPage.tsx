import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Eye, Plus, Trash2 } from "lucide-react";
import { usePaneDatasetId } from "../hooks/usePaneDatasetId";
import { usePaneContext } from "../contexts/PaneContext";
import { currentPaneView, usePaneStore } from "../store/paneStore";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { ASK_FOR_NOTES, chatKey, chatPreviewKey, chatsApi, chatsKey, type Chat, type ChatUpdate, type Privacy } from "../api/chats";
import { APP_NOTES_KEY, datasetNotesKey, notesApi } from "../api/notes";
import { filterSpecParams, type Finding } from "../api/health";
import { providersApi, type ProviderOut } from "../api/providers";
import { apiErrorDetail } from "../utils/apiError";
import { forgetStream, resumeReply, sendMessage, stopReply, unforgetStream, useChatStore } from "../store/chatStore";
import ConfirmDialog from "../components/common/ConfirmDialog";
import FilteredImagesModal from "../components/common/FilteredImagesModal";
import ModelPicker from "../components/providers/ModelPicker";
import Composer from "../components/chat/Composer";
import ContextDialog from "../components/chat/ContextDialog";
import MessageList from "../components/chat/MessageList";
import { useFindingActions } from "../components/health/useFindingActions";
import { useHealthReport } from "../components/health/useHealthReport";

/** The analyst chat (M12 phase 2): talk with an LLM about one dataset. Unkeyed in
 *  `PageRenderer`; the body keys itself on the dataset, so drafts and dialogs reset on
 *  a switch. A reply being written lives in `chatStore`, not here, so it carries on
 *  through a dataset switch, a pane move or leaving the page. See
 *  docs/dev/analyst/chat-ui.md. */
export default function ChatPage() {
  const datasetId = usePaneDatasetId();
  if (!datasetId) return null;
  return <ChatBody key={datasetId} datasetId={datasetId} />;
}

/** The chat a page shows, and how to change it. In a pane it is the pane's own
 *  (`PaneView.chatId`), so two panes on one dataset can show two chats and a pinned chat
 *  survives a reload with the layout; outside panes, the one each dataset last showed. */
function useOpenChat(datasetId: string): [string | undefined, (id: string | null) => void] {
  const ctx = usePaneContext();
  const routedChat = useChatStore((s) => s.openChat[datasetId]);
  const setOpenChat = useChatStore((s) => s.setOpenChat);
  const paneId = ctx?.paneId;
  const set = useCallback((id: string | null) => {
    if (!paneId) {
      setOpenChat(datasetId, id);
      return;
    }
    const view = currentPaneView(paneId);
    if (view) usePaneStore.getState().setView(paneId, { ...view, chatId: id ?? undefined });
  }, [paneId, datasetId, setOpenChat]);
  return [ctx ? ctx.view.chatId : routedChat, set];
}

function ChatBody({ datasetId }: { datasetId: string }) {
  const qc = useQueryClient();
  const [openChatId, setOpenChatId] = useOpenChat(datasetId);
  // Which chats are writing, as one string: a streamed piece of text then does not
  // re-render the list.
  const writingIds = useChatStore((s) => Object.keys(s.streams).join(","));

  const { data: chats = [], isLoading, error } = useQuery({ queryKey: chatsKey(datasetId), queryFn: () => chatsApi.list(datasetId) });
  const { data: providers = [] } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list, staleTime: 30_000 });

  const selected = chats.find((c) => c.id === openChatId) ?? chats[0] ?? null;

  const create = useMutation({
    mutationFn: () => chatsApi.create(datasetId, { provider_id: selected?.provider_id ?? providers[0]?.id ?? null }),
    onSuccess: (chat) => {
      // Into the list straight away, so the new chat opens before the refetch lands.
      qc.setQueryData<Chat[]>(chatsKey(datasetId), (old) => [chat, ...(old ?? [])]);
      qc.invalidateQueries({ queryKey: chatsKey(datasetId) });
      setOpenChatId(chat.id);
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not start a chat")),
  });

  return (
    <div className="chat-layout">
      <aside className="chat-list" aria-label="Chats">
        <div style={{ padding: "12px 10px 8px", display: "flex", alignItems: "center", gap: 8 }}>
          <strong style={{ fontSize: 13, flex: 1 }}>Analyst</strong>
          <button className="btn sm" onClick={() => create.mutate()} disabled={create.isPending}>
            <Plus size={13} /> New chat
          </button>
        </div>
        <ul className="chat-list-items">
          {chats.map((c) => (
            <li key={c.id}>
              <button
                className="chat-list-item"
                aria-current={c.id === selected?.id}
                onClick={() => setOpenChatId(c.id)}
              >
                <span className="t">{c.title || "New chat"}</span>
                <span className="m">
                  {writingIds.split(",").includes(c.id) ? "writing…" : `${c.message_count} message${c.message_count === 1 ? "" : "s"}`} · {new Date(c.updated_at).toLocaleDateString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="chat-main">
        {error ? (
          <div style={{ padding: 24, color: "var(--bad)" }}>{apiErrorDetail(error, "Could not load the chats")}</div>
        ) : isLoading ? (
          <div style={{ padding: 24, color: "var(--fg-mute)" }}>Loading…</div>
        ) : !selected ? (
          <EmptyState providers={providers} onCreate={() => create.mutate()} busy={create.isPending} />
        ) : (
          <ChatView key={selected.id} chat={selected} datasetId={datasetId} providers={providers} onDeleted={() => setOpenChatId(null)} />
        )}
      </section>
    </div>
  );
}

function EmptyState({ providers, onCreate, busy }: { providers: ProviderOut[]; onCreate: () => void; busy: boolean }) {
  return (
    <div style={{ margin: "auto", maxWidth: 460, padding: 24, textAlign: "center", fontSize: 13, color: "var(--fg-mute)", display: "flex", flexDirection: "column", gap: 12, alignItems: "center" }}>
      <h1 style={{ fontSize: 16, color: "var(--fg)", margin: 0 }}>Ask the analyst</h1>
      <p style={{ margin: 0 }}>
        Crucible finds the problems in this dataset; the analyst explains them and suggests what to
        do, with your goal and notes in mind. It suggests, and never changes anything itself.
      </p>
      {providers.length === 0 ? (
        <p style={{ margin: 0, color: "var(--warn)" }}>Add an LLM provider in Settings → LLM Providers first.</p>
      ) : (
        <button className="btn primary" onClick={onCreate} disabled={busy}>Start a chat</button>
      )}
    </div>
  );
}

function ChatView({ chat, datasetId, providers, onDeleted }: {
  chat: Chat;
  datasetId: string;
  providers: ProviderOut[];
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const stream = useChatStore((s) => s.streams[chat.id]);
  const { openInGallery, openFix } = useFindingActions(datasetId);
  const [draft, setDraft] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showing, setShowing] = useState<Finding | null>(null);

  const { data: detail } = useQuery({ queryKey: chatKey(chat.id), queryFn: () => chatsApi.get(chat.id) });
  const { data: report } = useHealthReport(datasetId);
  // The notes as they are now, so a suggestion whose note has gone shows as out of date.
  const { data: datasetNotes } = useQuery({ queryKey: datasetNotesKey(datasetId), queryFn: () => notesApi.dataset(datasetId).list() });
  const { data: appNotes } = useQuery({ queryKey: APP_NOTES_KEY, queryFn: () => notesApi.app().list() });
  const liveNotes = useMemo(
    () => (datasetNotes && appNotes ? new Map([...datasetNotes.items, ...appNotes.items].map((n) => [n.id, n])) : null),
    [datasetNotes, appNotes],
  );
  const findings = useMemo(
    () => new Map((report ? [...report.findings, ...report.dismissed] : []).map((f) => [f.id, f])),
    [report],
  );

  // The budget line follows the draft, a moment behind the typing.
  const typed = useDebouncedValue(draft, 600);
  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: [...chatPreviewKey(chat.id), typed],
    queryFn: ({ signal }) => chatsApi.preview(chat.id, typed, signal),
    placeholderData: keepPreviousData,
    enabled: !!chat.provider_id || showContext,
    retry: false,
  });

  // A reply still being written when the chat opened (a reload, another tab): pick
  // its stream back up. `resumeReply` does this once per reply.
  const lastMessage = detail?.messages[detail.messages.length - 1];
  useEffect(() => {
    if (lastMessage?.role === "assistant" && lastMessage.status === "partial") {
      resumeReply(qc, datasetId, chat.id, lastMessage.id);
    }
  }, [qc, datasetId, chat.id, lastMessage?.id, lastMessage?.role, lastMessage?.status]);

  // Returned, so the mutation stays pending until the refetches land: the dialog shows
  // the pending choice until then, rather than flicking back to the old one.
  const afterUpdate = () => Promise.all([
    qc.invalidateQueries({ queryKey: chatsKey(datasetId) }),
    qc.invalidateQueries({ queryKey: chatKey(chat.id) }),
    qc.invalidateQueries({ queryKey: chatPreviewKey(chat.id) }),
  ]);
  const update = useMutation({
    mutationFn: (body: ChatUpdate) => chatsApi.update(chat.id, body),
    onSuccess: afterUpdate,
    onError: (err) => toast.error(apiErrorDetail(err, "Could not change the chat")),
  });
  const remove = useMutation({
    mutationFn: async () => {
      // The server stops the reply itself; stop listening first so its end is not
      // read as a lost connection.
      forgetStream(chat.id);
      await chatsApi.remove(chat.id);
    },
    onSuccess: () => {
      onDeleted();
      qc.removeQueries({ queryKey: chatKey(chat.id) });
      qc.invalidateQueries({ queryKey: chatsKey(datasetId) });
    },
    onError: (err) => {
      unforgetStream(chat.id);
      toast.error(apiErrorDetail(err, "Could not delete the chat"));
    },
  });

  const provider = providers.find((p) => p.id === chat.provider_id) ?? null;
  const writing = !!stream && stream.phase !== "ended";
  const blockedReason =
    providers.length === 0 ? "Add an LLM provider in Settings → LLM Providers first."
    : !provider ? "Choose a provider for this chat."
    : !chat.model && !provider.default_model ? "Choose a model, or set the provider's default model."
    : null;

  return (
    <>
      <header style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 24px", borderBottom: "1px solid var(--line)", flexWrap: "wrap" }}>
        <TitleField chat={chat} onCommit={(title) => update.mutate({ title })} />
        <select
          className="select"
          aria-label="Provider"
          value={chat.provider_id ?? ""}
          // A new provider clears the model and the chat's privacy choice (the server
          // does the second): a choice made for one server must not follow to another.
          onChange={(e) => update.mutate({ provider_id: e.target.value || null, model: "" })}
          style={{ maxWidth: 200 }}
        >
          <option value="">— provider —</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>{p.name}{p.is_billed ? " (billed)" : ""}</option>
          ))}
        </select>
        {provider && (
          <div style={{ width: 240 }}>
            {/* Keyed on the provider, so a model still waiting out its debounce is dropped
                with the provider rather than saved onto the new one. */}
            <ModelField key={provider.id} chat={chat} provider={provider} onCommit={(model) => update.mutate({ model })} />
          </div>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button
            className="btn sm"
            onClick={() => {
              // Notes, the goal or captions may have changed in another pane since the
              // preview was fetched: the dialog promises what the next send would send.
              qc.invalidateQueries({ queryKey: chatPreviewKey(chat.id) });
              setShowContext(true);
            }}
          >
            <Eye size={13} /> What gets sent
          </button>
          <button className="icon-btn" title="Delete this chat" aria-label="Delete this chat" onClick={() => setConfirmDelete(true)}>
            <Trash2 size={14} />
          </button>
        </span>
      </header>

      <MessageList
        chatId={chat.id}
        datasetId={datasetId}
        liveNotes={liveNotes}
        onAskForNotes={!blockedReason && !stream ? () => void sendMessage(qc, datasetId, chat.id, ASK_FOR_NOTES) : undefined}
        messages={detail?.messages ?? []}
        stream={stream}
        findings={findings}
        onShowFinding={setShowing}
        onOpenGallery={openInGallery}
        onFix={openFix}
      />

      <Composer
        draft={draft}
        onDraft={setDraft}
        onSend={(text) => sendMessage(qc, datasetId, chat.id, text)}
        onStop={() => { if (stream?.messageId) void stopReply(chat.id, stream.messageId); }}
        writing={writing}
        stopping={!!stream?.stopping}
        canStop={!!stream?.messageId}
        // Until a reply's stored copy has replaced its stream, a send would be refused.
        canSend={!blockedReason && !stream}
        blockedReason={blockedReason}
        budget={provider ? preview?.budget : undefined}
        estimate={preview?.manifest.estimated_tokens}
      />

      {showContext && (
        <ContextDialog
          chat={chat}
          preview={preview}
          loading={previewLoading}
          busy={update.isPending}
          providerPrivacy={provider?.analyst_privacy_effective ?? null}
          providerName={provider?.name ?? null}
          onPrivacy={(privacy: Privacy | null, done: () => void) => update.mutate({ privacy }, { onSettled: done })}
          onClose={() => setShowContext(false)}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Delete this chat?"
          message="Its messages go for good. Notes it led to stay, and so does what it used of today's budget."
          confirmLabel="Delete"
          danger
          onConfirm={() => { setConfirmDelete(false); remove.mutate(); }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
      {showing?.filter_spec && (
        <FilteredImagesModal
          title={showing.title}
          params={filterSpecParams(showing.filter_spec)}
          datasetId={datasetId}
          onClose={() => setShowing(null)}
        />
      )}
    </>
  );
}

/** The chat's title, edited in place; saved on Enter or leaving the field. */
function TitleField({ chat, onCommit }: { chat: Chat; onCommit: (t: string) => void }) {
  const [value, setValue] = useState(chat.title);
  const [prev, setPrev] = useState(chat.title);
  // The first message names an untitled chat on the server: take that in, unless
  // the user is mid-edit.
  if (chat.title !== prev) {
    setPrev(chat.title);
    if (value === prev) setValue(chat.title);
  }
  const commit = () => {
    const t = value.trim();
    if (t !== chat.title) onCommit(t);
  };
  return (
    <input
      className="input"
      aria-label="Chat title"
      placeholder="New chat"
      maxLength={200}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
      style={{ flex: "1 1 160px", minWidth: 120, fontWeight: 600 }}
    />
  );
}

/** The chat's model. `ModelPicker` reports every keystroke in its text mode, so the
 *  value is saved once typing pauses, not per key. Blank means the provider's default. */
function ModelField({ chat, provider, onCommit }: { chat: Chat; provider: ProviderOut; onCommit: (m: string) => void }) {
  const [value, setValue] = useState(chat.model);
  const [prev, setPrev] = useState(chat.model);
  // A server change (a new provider clears the model) replaces the field, unless the
  // user has typed past it: their keystrokes are not thrown away by a refetch.
  if (chat.model !== prev) {
    setPrev(chat.model);
    if (value === prev) setValue(chat.model);
  }
  const settled = useDebouncedValue(value, 700);
  const committed = useRef(chat.model);
  useEffect(() => {
    if (settled === committed.current) return;
    committed.current = settled;
    if (settled !== chat.model) onCommit(settled);
  }, [settled, chat.model, onCommit]);
  return (
    <ModelPicker
      value={value}
      onChange={setValue}
      providerId={provider.id}
      baseUrl={provider.base_url}
      placeholder={provider.default_model ? `Default: ${provider.default_model}` : "Model name"}
      emptyLabel={provider.default_model ? `Provider default (${provider.default_model})` : undefined}
    />
  );
}
