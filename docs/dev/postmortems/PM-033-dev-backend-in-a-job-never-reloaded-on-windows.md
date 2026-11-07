# PM-033: The dev backend ran in a job and never reloaded on Windows

### Symptom

Under `manage.ps1 dev` on Windows, TopBar **Restart** returned 204 and wrote
the reload trigger file `_dev_reload_trigger.py`, but the backend never came back: `GET /api/v1/health`
kept answering with the old `start_time`, so the restart overlay waited for ever. The same
hang follows any saved `.py` under `backend/`, since a reload is a reload whatever starts it.
A Shutdown sent after the hang killed the stuck worker, but the reloader survived and
started a new one. Found by the owed hand check on 2026-11-07. The bug dates from
2026-05-20 (`6f66f74`), when `Cmd-Dev` first ran uvicorn inside `Start-Job`.

### Root cause

uvicorn's reloader restarts its worker on Windows with `os.kill(pid, CTRL_C_EVENT)` and then
`join()`s it. That event only lands in a process attached to a real console that has
Ctrl+C enabled. `Cmd-Dev` ran uvicorn in a PowerShell `Start-Job`, a hidden
`powershell.exe -s` child whose processes never got the event. The worker kept serving and
the reloader blocked in `join()` for good. A bare `uvicorn --reload` started in its own console
on the same machine restarted in 2 s, which isolates the job as the cause.

### Generalizable rule

Flag any launcher that runs a supervised child process (a reloader, a watcher, anything
that stops its child with a console control event) under `Start-Job`, a hidden or redirected
process, or a service. On Windows, signals are console events, so the child needs a console
of its own. It needs one that it shares with nothing that should outlive the event, because
`CTRL_C_EVENT` reaches every process on that console. "It starts and serves" proves nothing
about whether it can be stopped or restarted.

### Why it wasn't caught the first time

No gate runs `manage.ps1`; CI only parse-checks it. The Linux path (`manage.sh`'s background
subshell) has signals and was fine, and every earlier check of dev Restart ran on Linux. The
first Windows attempt ran in a headless harness, where even a bare uvicorn hung. So "hangs
on Windows" was read as a harness artefact and the check was deferred to a real terminal.
Hot reload had seemingly never worked on Windows dev, and no one had noticed it.

### Fix

`Cmd-Dev` starts the backend with `Start-Process` in a minimized console window of its own
(`powershell -NoExit`, titled `Crucible backend :<port>`), with no restart loop. A `finally`
around Vite closes that window's tree with `taskkill /T` when Vite exits or gets Ctrl+C.
It was checked in a real console window on 2026-11-07: two Restarts each back in ~2 s
with Vite unharmed and still proxying, Shutdown ending the backend, and the backend window
closed when Vite exited. See `docs/dev/http-edge.md` § Server control endpoints, its **Dev mode** paragraph.

### Status & date

MITIGATED — still nothing but a hand check runs `manage.ps1 dev` on Windows.
Last reviewed for staleness: 2026-11-07.
