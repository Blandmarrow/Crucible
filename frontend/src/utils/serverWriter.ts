import toast from "react-hot-toast";

/** Browsers refuse a `keepalive` request once in-flight keepalive bodies pass 64 KiB. */
const KEEPALIVE_LIMIT_BYTES = 60_000;
const FIRST_RETRY_DELAY_MS = 1000;
const SLOW_RETRY_DELAY_MS = 5000;

type Method = "PUT" | "DELETE";

interface PendingWrite {
  method: Method;
  body?: string;
}

interface Channel {
  /** The newest write the server hasn't confirmed. Each write carries the whole value,
   *  so a newer one simply replaces it, and it stays here through any number of retries. */
  owed: PendingWrite | null;
  /** The write on the wire, and whether it would survive the page closing. */
  sending: PendingWrite | null;
  sendingKeepalive: boolean;
  draining: Promise<void> | null;
  /** The server has refused twice: a read now would return what this write will replace. */
  failing: boolean;
  signalFailing: () => void;
  failingSignal: Promise<void>;
}

const channels = new Map<string, Channel>();
let keepaliveBytesInFlight = 0;

class WriteError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function post(path: string, { method, body }: PendingWrite, keepalive: boolean, size: number): Promise<void> {
  if (keepalive) keepaliveBytesInFlight += size;
  try {
    // fetch and not the axios client: only fetch has `keepalive`, which is what lets a
    // write made while the tab closes outlive the page.
    const res = await fetch(`/api/v1${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body,
      keepalive,
    });
    if (!res.ok) throw new WriteError(res.status, `${method} ${path} answered ${res.status}`);
  } finally {
    if (keepalive) keepaliveBytesInFlight -= size;
  }
}

function send(path: string, write: PendingWrite, channel?: Channel): Promise<void> {
  const size = write.body === undefined ? 0 : new Blob([write.body]).size;
  // The limit is on everything in flight, so a flush while another write is on the wire counts it.
  const keepalive = keepaliveBytesInFlight + size < KEEPALIVE_LIMIT_BYTES;
  if (channel) {
    channel.sending = write;
    channel.sendingKeepalive = keepalive;
  }
  return post(path, write, keepalive, size);
}

async function drain(path: string, channel: Channel): Promise<void> {
  let failures = 0;
  while (channel.owed) {
    const write = channel.owed;
    try {
      await send(path, write, channel);
      if (channel.owed === write) channel.owed = null;
      if (failures >= 2) toast.success("Your changes are saved.", { id: path });
      failures = 0;
      channel.failing = false;
    } catch (err) {
      const status = err instanceof WriteError ? err.status : 0;
      if (status >= 400 && status < 500) {
        // Retrying can't help. A 404 means what this saved to has been deleted, which is
        // no news to whoever deleted it.
        if (channel.owed === write) channel.owed = null;
        if (status !== 404) toast.error("The app refused to save your changes.", { id: path });
        continue;
      }
      failures += 1;
      if (failures === 2) {
        channel.failing = true;
        channel.signalFailing();
        toast.error("Couldn't save your changes to the app. Still trying: keep this page open.", { id: path, duration: Infinity });
      }
      await new Promise((resolve) => setTimeout(resolve, failures === 1 ? FIRST_RETRY_DELAY_MS : SLOW_RETRY_DELAY_MS));
    } finally {
      channel.sending = null;
    }
  }
}

function startDrain(path: string, channel: Channel): void {
  if (channel.draining) return;
  channel.failing = false;
  channel.failingSignal = new Promise((resolve) => { channel.signalFailing = resolve; });
  channel.draining = drain(path, channel).finally(() => { channel.draining = null; });
}

// Capture runs before a mounted hook's own `pagehide` flush, so that flush sees `pageHiding`.
// Nothing queued or waiting to retry would ever run after this, so it all goes now. The
// page may yet come back from the back/forward cache, and sending the same value twice is
// harmless, so what's owed stays owed until a response confirms it.
let pageHiding = false;
window.addEventListener("pagehide", () => {
  pageHiding = true;
  for (const [path, channel] of channels) {
    const owed = channel.owed;
    if (!owed) continue;
    if (channel.sending === owed && channel.sendingKeepalive) continue;
    void send(path, owed).catch(() => {});
  }
}, { capture: true });
window.addEventListener("pageshow", () => {
  pageHiding = false;
  for (const [path, channel] of channels) {
    if (channel.owed) startDrain(path, channel);
  }
});

/** Sends a write to `path` (under `/api/v1`), one at a time per path so a slow request
 *  can't land after a newer one. Never rejects. A failure keeps retrying until it lands or
 *  a newer write replaces it, and the user is told if it's taking more than one retry. */
export function writeToServer(path: string, method: Method, value?: object): void {
  const write: PendingWrite = { method, body: value === undefined ? undefined : JSON.stringify(value) };
  let channel = channels.get(path);
  if (!channel) {
    channel = {
      owed: null, sending: null, sendingKeepalive: false, draining: null,
      failing: false, signalFailing: () => {}, failingSignal: Promise.resolve(),
    };
    channels.set(path, channel);
  }
  channel.owed = write;
  if (pageHiding) {
    void send(path, write).catch(() => {});
    return;
  }
  startDrain(path, channel);
}

/** Resolves once writes to `path` have landed, so a read can't return what a write still
 *  on its way is about to replace (a dialog closed and straight away reopened). Rejects
 *  if they're failing, because a form loaded now would start from the old value and its
 *  next save would throw away the one still being retried. */
export async function serverWritesSettled(path: string): Promise<void> {
  const channel = channels.get(path);
  if (!channel?.draining) return;
  if (!channel.failing) await Promise.race([channel.draining, channel.failingSignal]);
  if (channel.failing) throw new Error(`A save to ${path} hasn't landed yet`);
}
