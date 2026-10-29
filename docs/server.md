# Server

The server process's life: launching it and what the browser shows while it starts, restarting or stopping it from the app and what happens to running jobs when you do, and the database backup it takes at startup. The top bar's job progress, the Logs page and the rest of the app shell are covered in [Workspace](workspace.md).

Available from: `manage start` in a terminal (or **Start** in `Crucible.bat`), and the two buttons at the right of the top bar.

## Starting up

`manage start` — `.\manage.ps1 start`, `./manage.sh start`, or **Start** in `Crucible.bat` — opens your browser on `http://localhost:8000` immediately, before the server is ready. Until it is, that page is a placeholder: the Crucible mark animating over "Starting Crucible…". It swaps itself for the app the moment the server answers, so there is nothing to click and no need to reload. A first launch, or the first one after an update, also rebuilds the frontend and loads PyTorch, so a couple of minutes is normal; past a minute the page says as much and offers a manual **Reload now** button.

Only `start` does this — `dev` leaves your browser alone. A few other things worth knowing:

- To launch without a browser (a headless machine, or you simply have the tab open already), set `CRUCIBLE_NO_BROWSER=1`: `CRUCIBLE_NO_BROWSER=1 ./manage.sh start`, or `$env:CRUCIBLE_NO_BROWSER=1` before `.\manage.ps1 start` on Windows. Nothing opens and no placeholder runs; startup is otherwise identical.
- If something else already holds port 8000 — usually a copy of Crucible you forgot to stop — the placeholder steps aside silently and no browser opens, so what you see is the server's own "address already in use" error rather than a page from the other instance.
- **Use `localhost:8000`, not `0.0.0.0:8000`.** Both launchers print the right URL, but with LAN access on, uvicorn's own banner (`Uvicorn running on http://0.0.0.0:8000`) prints just after it and is usually the last clickable link in the terminal. `0.0.0.0` is a bind address rather than somewhere to browse to, and browsers treat it as a *non-secure origin*, which withholds a handful of APIs Crucible uses. Land on it anyway and Crucible now bounces you to the `localhost` equivalent, keeping your port, path and query — so the wrong link costs you nothing.
- **Browsing from another machine on your network is off by default.** Crucible listens on this machine only until you opt in with `start --lan` (`.\manage.ps1 start -Lan` on Windows) or `CRUCIBLE_HOST=0.0.0.0` in `.env`; the launcher then prints the address to use and a reminder that there is no login. Earlier versions listened on the network without asking, so if you used to browse from another device, opt in once. See [Access from other devices](../README.md#access-from-other-devices-lan) for host names and the security trade-off.
- **A LAN address** (`http://192.168.1.5:8000`, say) is also a non-secure origin, and that one is left alone — it is a deliberate choice about where you want to reach the app from, and rewriting it would point your browser at the wrong machine. Everything works there; copy buttons fall back to an older browser mechanism, which is invisible in use.

## Restarting & shutting down

Two buttons sit at the right of the top bar. Both ask for confirmation first, and both are disabled while the other is in progress.

- **Restart** (circular arrow) — restarts the server in place, without needing terminal access. Any running jobs are interrupted. A full-screen overlay covers the app while it waits, and the page reloads itself the moment the server answers. If it takes longer than about 25 seconds the overlay explains what it is waiting for and offers a manual **Reload now** button — it never gives up on its own, because a cold start that loads PyTorch can legitimately take a while.
- **Shut down** (power icon) — stops the server process. On Windows the terminal window closes by itself after a clean shutdown.

> **The server only comes back on its own if you started it with `manage`** (`manage.ps1 start` / `./manage.sh start`, or `Crucible.bat`). Those scripts run the server in a loop that relaunches it after a restart. If you started uvicorn by hand, Restart stops the server and you will need to start it again yourself.

If a job is running when you restart or shut down, it is asked to stop and given about **10 seconds** to do so. Snapshot, restore and branch-checkout jobs get up to **2 minutes**, because stopping one halfway would leave the dataset half-changed. After that the server stops it regardless, so a restart can take that much longer while a job is running. Jobs still waiting in the queue are not started.

Jobs that were running when the server stopped are not resumed — they are marked failed with "Interrupted by server shutdown or restart" so nothing shows as stuck forever in the [Logs](workspace.md#logs) history. Work a job finished before it stopped is kept, as it is when you cancel one. If the app lost touch with the server while a job finished, the top bar and the open page catch up by themselves once the connection is back, with no reload needed.

## Database backups

Everything Crucible knows — captions, scores, detections, version history — lives in one SQLite file (`dataset_manager.db`, next to `manage.ps1`/`manage.sh`). When the server starts it checks that file for corruption and, if it is healthy, saves a timestamped copy into a `backups/` folder beside it, keeping the **five most recent**. This happens in the background a moment after startup; nothing waits on it.

Three things worth knowing:

- A start within **15 minutes** of the newest backup does nothing — no check, no copy. Only five copies are kept, so a run of quick restarts would otherwise discard the whole backup history in a few minutes, which is the opposite of what it is for. Restarting to force a fresh copy therefore does not work; a backup is a periodic safety net, not a snapshot button. For a point-in-time copy you control, use [dataset versioning](versioning.md).
- A database that fails the integrity check is **not** backed up. That is deliberate — copying a damaged file in would push the newest good backup one slot closer to deletion. The failure is written to the server's console output.
- Backups are full copies on the same disk. They protect against a bad edit or a corrupted database, not against losing the drive. If a dataset matters, copy a backup somewhere else.

To restore one, stop the server, rename the backup over `dataset_manager.db`, and start again. Image files are not part of the backup — for those, use [dataset versioning](versioning.md).
