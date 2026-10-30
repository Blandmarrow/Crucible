import toast from "react-hot-toast";

/** Browsers refuse a `keepalive` request once in-flight keepalive bodies pass 64 KiB. */
const KEEPALIVE_LIMIT_BYTES = 60_000;
const RETRY_DELAY_MS = 1000;

type Method = "PUT" | "DELETE";

interface PendingWrite {
  method: Method;
  body?: string;
}

interface Channel {
  /** Only the latest matters: a blob write replaces whatever was queued before it. */
  next: PendingWrite | null;
  draining: Promise<void> | null;
}

const channels = new Map<string, Channel>();

// Capture runs before a mounted hook's own `pagehide` flush, so that flush sees this.
let pageHiding = false;
window.addEventListener("pagehide", () => { pageHiding = true; }, { capture: true });
window.addEventListener("pageshow", () => { pageHiding = false; });

async function send(path: string, { method, body }: PendingWrite): Promise<void> {
  // fetch and not the axios client: only fetch has `keepalive`, which is what lets a
  // write made while the tab closes outlive the page.
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body,
    keepalive: body === undefined || new Blob([body]).size < KEEPALIVE_LIMIT_BYTES,
  });
  if (!res.ok) throw new Error(`${method} ${path} answered ${res.status}`);
}

async function drain(path: string, channel: Channel): Promise<void> {
  while (channel.next) {
    const write = channel.next;
    channel.next = null;
    try {
      await send(path, write);
    } catch {
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
      // A newer write supersedes this one, and will carry the whole value anyway.
      if (channel.next) continue;
      try {
        await send(path, write);
      } catch {
        toast.error("Couldn't save your changes to the app. They will be lost if you reload now.", { id: path });
      }
    }
  }
}

/** Sends a write to `path` (under `/api/v1`), one at a time per path so a slow request
 *  can't land after a newer one. Never rejects: a failure is reported to the user. */
export function writeToServer(path: string, method: Method, value?: object): void {
  const write: PendingWrite = { method, body: value === undefined ? undefined : JSON.stringify(value) };
  if (pageHiding) {
    // Nothing queued would ever run now, so this can't wait its turn.
    void send(path, write).catch(() => {});
    return;
  }
  let channel = channels.get(path);
  if (!channel) {
    channel = { next: null, draining: null };
    channels.set(path, channel);
  }
  channel.next = write;
  if (!channel.draining) {
    const current = channel;
    current.draining = drain(path, current).finally(() => { current.draining = null; });
  }
}

/** Resolves once writes to `path` have finished, so a read can't return what a write
 *  still on its way is about to replace (a dialog closed and straight away reopened). */
export function serverWritesSettled(path: string): Promise<void> {
  return channels.get(path)?.draining ?? Promise.resolve();
}
