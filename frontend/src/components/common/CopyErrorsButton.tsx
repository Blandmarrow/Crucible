import { useEffect, useRef, useState } from "react";
import { copyText } from "../../utils/clipboard";
import { formatErrorsForCopy, type ErrorEntry } from "../../store/errorConsoleStore";

/**
 * The "Copy Errors" button, shared by `ErrorConsole` and the Logs page's Errors
 * tab — the two renderings of the same error list, whose handlers were
 * byte-identical (and identically broken: a `.catch(() => {})` that caught
 * nothing, since the missing-API TypeError is synchronous and precedes the promise).
 *
 * Feedback is a transient label swap rather than a toast on purpose: this surface
 * reports the app's own failures, and a toast about the error console is noise on
 * top of the thing the user is already reading.
 */
export default function CopyErrorsButton({ errors }: { errors: ErrorEntry[] }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const flash = (next: "ok" | "fail") => {
    setState(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1500);
  };

  return (
    <button
      className="btn sm"
      // Keep this callback trivial: a throw inside it is an unhandled rejection,
      // and `copyText`'s never-throws invariant covers only `copyText` itself.
      onClick={() => void copyText(formatErrorsForCopy(errors)).then((ok) => flash(ok ? "ok" : "fail"))}
      title="Copy all errors to clipboard"
    >
      {state === "ok" ? "Copied" : state === "fail" ? "Copy failed" : "Copy Errors"}
    </button>
  );
}
