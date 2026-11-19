import { memo, useMemo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Finding } from "../../api/health";
import { SEVERITY_CLASS } from "../health/severity";

/** `[[finding:id]]` in a reply, as `backend/services/chat_runner.py::FINDING_REF_RE`. */
const FINDING_REF = /\[\[finding:([a-z0-9_]+)\]\]/g;
const FINDING_HREF = "#crucible-finding-";

/** A reply's text as markdown. Raw HTML is never rendered (react-markdown escapes it
 *  unless a raw plugin is added, and none is), and an image is never loaded: a reply
 *  can quote captions, which are untrusted, and an image URL is a way to send data
 *  out. A `[[finding:id]]` naming a known finding becomes a chip built from
 *  Crucible's own data; an unknown id stays plain text, so the model cannot invent
 *  one. See docs/dev/analyst/chat-ui.md § Rendering a reply. */
function ChatMarkdown({ text, findings }: { text: string; findings: Map<string, Finding> }) {
  const source = useMemo(
    () => text.replace(FINDING_REF, (whole, id: string) => (findings.has(id) ? `[${id}](${FINDING_HREF}${id})` : whole)),
    [text, findings],
  );

  const components = useMemo<Components>(() => ({
    a: ({ href, children }) => {
      if (href?.startsWith(FINDING_HREF)) {
        const f = findings.get(href.slice(FINDING_HREF.length));
        if (f) return <FindingChip finding={f} />;
        return <>{children}</>;
      }
      return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
    },
    img: ({ alt, src }) => (
      <span style={{ color: "var(--fg-dim)" }} title={typeof src === "string" ? src : undefined}>
        [image not loaded{alt ? `: ${alt}` : ""}]
      </span>
    ),
  }), [findings]);

  return (
    <div className="chat-md">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>{source}</Markdown>
    </div>
  );
}

export default memo(ChatMarkdown);

function FindingChip({ finding: f }: { finding: Finding }) {
  return (
    <span
      className={`badge dot ${SEVERITY_CLASS[f.severity]}`}
      data-testid={`finding-ref-${f.id}`}
      title={`${f.title}: ${f.count.toLocaleString()} of ${f.total.toLocaleString()}`}
      style={{ verticalAlign: "baseline" }}
    >
      {f.title} · {f.count.toLocaleString()}
    </span>
  );
}
