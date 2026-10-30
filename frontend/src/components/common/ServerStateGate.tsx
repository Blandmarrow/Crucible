import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

interface Props<T> {
  queryKey: readonly unknown[];
  load: () => Promise<T>;
  /** What failed to load, for the error message. */
  label: string;
  /** Wraps the loading and error states, for a dialog that needs its frame around them. */
  frame?: (content: ReactNode) => ReactNode;
  children: (value: T) => ReactNode;
}

/**
 * Holds back a form that reads saved state from the server in its `useState` initializers.
 *
 * Without it the form would start from defaults, and its auto-save could then write those
 * over the real data. So nothing mounts until the value has arrived, a failure offers Retry
 * rather than falling back to defaults, and the first value is kept: the form owns the
 * state from then on, so a refetch must neither remount it nor change what it was given.
 *
 * The query keeps nothing between mounts (`gcTime: 0`), or reopening a form would serve the
 * value from before the edits it just saved. See docs/dev/app-stored-data.md.
 */
export default function ServerStateGate<T>({ queryKey, load, label, frame = (c) => c, children }: Props<T>) {
  const query = useQuery({
    queryKey,
    queryFn: load,
    gcTime: 0,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const [latched, setLatched] = useState<{ value: T } | null>(null);
  if (latched === null && query.data !== undefined) setLatched({ value: query.data });

  if (latched !== null) return <>{children(latched.value)}</>;

  if (query.isError) {
    return (
      <>
        {frame(
          <div style={{ padding: 40, color: "var(--fg-mute)" }}>
            <p style={{ margin: "0 0 12px" }}>Couldn't load {label}. Nothing has been changed.</p>
            <button className="btn sm" onClick={() => query.refetch()} disabled={query.isFetching}>
              {query.isFetching ? "Retrying…" : "Retry"}
            </button>
          </div>,
        )}
      </>
    );
  }

  return <>{frame(<div style={{ padding: 40, color: "var(--fg-mute)" }}>Loading…</div>)}</>;
}
