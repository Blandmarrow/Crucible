import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { PRIVACY_CATEGORIES, PRIVACY_LABELS, type PrivacyCategory } from "../../api/chats";
import { providersApi, type ProviderOut } from "../../api/providers";
import { settingsApi, type Thresholds } from "../../api/settings";
import { apiErrorDetail } from "../../utils/apiError";
import NumberField from "../common/NumberField";

const MIN_BUDGET = 1_000;
const MAX_BUDGET = 2 ** 31 - 1;
const clampBudget = (n: number) => Math.min(MAX_BUDGET, Math.max(MIN_BUDGET, Math.round(n)));

/** Settings → Analyst: the two token budgets, and what each provider may be sent by
 *  default. The rules behind both are docs/dev/analyst/chat.md § Privacy: three layers
 *  and docs/dev/analyst/sending.md § Budgets. */
export default function AnalystPanel() {
  return (
    <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <p style={{ fontSize: 12.5, color: "var(--fg-mute)", margin: 0 }}>
        The analyst is the chat on each dataset's Analyst page. It reads the dataset's findings, and
        whatever else the settings below allow, and suggests what to do.
      </p>
      <Budgets />
      <ProviderPrivacy />
      <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>
        <strong style={{ color: "var(--fg)" }}>Reasoning models need room.</strong> A model that thinks
        before it answers spends its thinking from the same Max tokens as the reply. Gemini 2.5 Flash
        stopped after two sentences at 1,024 and finished at 2,048. If replies end early with “cut off
        at Max tokens”, raise the provider's Max tokens on the LLM Providers tab.
      </div>
    </div>
  );
}

function Budgets() {
  const qc = useQueryClient();
  const uid = useId();
  const { data: thresholds } = useQuery({ queryKey: ["settings", "thresholds"], queryFn: settingsApi.getThresholds, staleTime: 60_000 });
  const [draft, setDraft] = useState<Pick<Thresholds, "analyst_chat_budget_tokens" | "analyst_daily_budget_tokens"> | null>(null);
  const chat = draft?.analyst_chat_budget_tokens ?? thresholds?.analyst_chat_budget_tokens ?? 200_000;
  const daily = draft?.analyst_daily_budget_tokens ?? thresholds?.analyst_daily_budget_tokens ?? 1_000_000;
  const changed = !!thresholds && (chat !== thresholds.analyst_chat_budget_tokens || daily !== thresholds.analyst_daily_budget_tokens);

  const save = useMutation({
    mutationFn: () => settingsApi.updateThresholds({ analyst_chat_budget_tokens: chat, analyst_daily_budget_tokens: daily }),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "thresholds"], data);
      setDraft(null);
      // Every open chat's budget line reads these.
      qc.invalidateQueries({ queryKey: ["chat-preview"] });
      toast.success("Budgets saved");
    },
    onError: (err) => toast.error(apiErrorDetail(err, "Could not save the budgets")),
  });

  return (
    <section>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 4px" }}>Token budgets</h3>
      <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
        Only a billed provider meets these; a free one is never capped. Before each send Crucible adds
        the context to a full-length reply, and refuses the send if that could pass either budget. A
        deleted chat's tokens still count toward today.
      </p>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
        <div>
          <label className="label" htmlFor={`${uid}-chat`}>Per chat (tokens)</label>
          <NumberField
            id={`${uid}-chat`}
            className="input"
            min={MIN_BUDGET}
            step={1000}
            value={chat}
            clamp={clampBudget}
            onCommit={(n) => setDraft({ analyst_chat_budget_tokens: n, analyst_daily_budget_tokens: daily })}
            style={{ width: 160 }}
          />
        </div>
        <div>
          <label className="label" htmlFor={`${uid}-day`}>Per day (tokens)</label>
          <NumberField
            id={`${uid}-day`}
            className="input"
            min={MIN_BUDGET}
            step={1000}
            value={daily}
            clamp={clampBudget}
            onCommit={(n) => setDraft({ analyst_chat_budget_tokens: chat, analyst_daily_budget_tokens: n })}
            style={{ width: 160 }}
          />
        </div>
        <button className="btn primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save budgets</button>
      </div>
    </section>
  );
}

function ProviderPrivacy() {
  const { data: providers, isLoading } = useQuery({ queryKey: ["providers"], queryFn: providersApi.list, staleTime: 30_000 });
  return (
    <section>
      <h3 style={{ fontSize: 13, fontWeight: 600, margin: "0 0 4px" }}>What each provider may be sent</h3>
      <p style={{ fontSize: 12, color: "var(--fg-mute)", margin: "0 0 10px" }}>
        By default a billed provider gets numbers only, and a free one gets everything. A chat can make
        its own choice under “What gets sent”; a chat that has not follows the boxes here. The default
        comes from the provider's <em>billed</em> box, which overrides the guess from its address: a
        paid service reached through a tunnel on <code style={{ fontSize: 11 }}>localhost</code> looks
        local, so tick <em>billed</em> on it, or it is sent everything.
      </p>
      {isLoading && <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>Loading…</div>}
      {providers && providers.length === 0 && (
        <div style={{ fontSize: 12.5, color: "var(--fg-dim)" }}>No providers yet. Add one on the LLM Providers tab.</div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {providers?.map((p) => <ProviderRow key={p.id} provider={p} />)}
      </div>
    </section>
  );
}

function ProviderRow({ provider: p }: { provider: ProviderOut }) {
  const qc = useQueryClient();
  const uid = useId();
  const custom = p.analyst_privacy !== null;

  const update = useMutation({
    mutationFn: (body: { analyst_privacy?: Record<string, boolean>; analyst_privacy_reset?: boolean }) =>
      providersApi.update(p.id, body),
    // Returned, so the boxes show the pending choice until the list is refetched.
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: ["providers"] }),
      qc.invalidateQueries({ queryKey: ["chat-preview"] }),
    ]),
    onError: (err) => toast.error(apiErrorDetail(err, "Could not save the provider's privacy")),
  });
  // The choice being saved, from the click until the list is refetched (a controlled
  // checkbox is put back at the end of the click otherwise).
  const [pending, setPending] = useState<Record<string, boolean> | null>(null);
  const shown = pending ?? p.analyst_privacy_effective;

  const toggle = (cat: PrivacyCategory, on: boolean) => {
    const next: Record<string, boolean> = {};
    for (const c of PRIVACY_CATEGORIES) if (c !== "images") next[c] = c === cat ? on : !!shown[c];
    setPending(next);
    update.mutate({ analyst_privacy: next }, { onSettled: () => setPending(null) });
  };

  return (
    <div className="card" style={{ padding: "10px 14px" }} data-testid={`analyst-privacy-${p.id}`}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 13 }}>{p.name}</strong>
        <span className={`badge ${p.is_billed ? "warn" : ""}`}>{p.is_billed ? "billed" : "free"}</span>
        <span style={{ fontSize: 11.5, color: "var(--fg-dim)" }}>
          {custom || pending ? "Your choice" : `The ${p.is_billed ? "billed" : "free"} defaults`}
        </span>
        {custom && (
          <button
            className="btn sm ghost"
            style={{ marginLeft: "auto" }}
            disabled={update.isPending}
            onClick={() => update.mutate({ analyst_privacy_reset: true })}
          >
            Back to the defaults
          </button>
        )}
      </div>
      <fieldset className="cmp-fieldset" disabled={update.isPending}>
        <legend className="sr-only">What {p.name} may be sent</legend>
        <div style={{ display: "flex", gap: "6px 16px", flexWrap: "wrap" }}>
          {PRIVACY_CATEGORIES.map((c) => (
            <label key={c} htmlFor={`${uid}-${c}`} title={PRIVACY_LABELS[c].help} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12.5, color: c === "images" ? "var(--fg-dim)" : "var(--fg)" }}>
              <input
                id={`${uid}-${c}`}
                type="checkbox"
                checked={!!shown[c]}
                disabled={c === "images"}
                onChange={(e) => toggle(c, e.target.checked)}
              />
              {PRIVACY_LABELS[c].label}
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
