# PM-019: `2>$null` on a pip warning aborted every fresh Windows setup

### Symptom

On a clean Windows checkout with an NVIDIA GPU, `manage.ps1 setup` died partway through
step `[4/7]`, immediately after the user answered `Y` to the GPU PyTorch prompt:

```
  Installing PyTorch (cu132) from PyTorch wheel index...
pip.exe : WARNING: Skipping torch as it is not installed.
At D:\Crucible\manage.ps1:284 char:13
+             & "$ROOT\venv\Scripts\pip.exe" uninstall -y torch torchvi ...
+             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (WARNING: Skippi... not installed.:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
```

No PyTorch, no `requirements.txt`, no frontend build — setup never reached any of it, and
re-running reproduced it exactly. The text that killed the script is a pip **warning**
about a no-op, not an error: nothing had actually gone wrong.

### Root cause

`manage.ps1` sets `$ErrorActionPreference = "Stop"` at file scope. On PowerShell 5.1,
**redirecting a native command's stderr is what makes PowerShell wrap those lines in a
`NativeCommandError` ErrorRecord** — unredirected, stderr goes straight to the console and
PowerShell never sees it. Under `Stop` that record is terminating, and it is raised
*before* the redirection can discard it. So `2>$null` was not a failed suppression; it was
the trigger. Removing it would also have fixed the abort (at the cost of printing the
warning). `--quiet` was no help either: one `-q` suppresses INFO, not WARNING.

What made it fire on *every* fresh install is the state asymmetry. The uninstall exists
for PM-004's reason — a `+cpu` build satisfies `torch>=2.7`, so the CUDA install would be
a silent no-op unless torch is removed first. But on a fresh venv torch is **not installed
by definition**, so the uninstall is *always* the no-op case, and pip always says so on
stderr. On an `update` run, torch is present, the uninstall succeeds silently, and nothing
happens. The path was only ever exercised in the state that cannot fail.

### This was the third encounter with the same trap

The trap was already known in this file, twice over, and the fix never generalized:

- Commit `a5e253c` (2026-07-19) wrapped the `$hasCuda` probe in an empty `try/catch`
  *specifically* because `import torch` on a fresh venv writes a traceback to stderr and
  aborts setup — and added this broken `2>$null` **twelve lines below it, in the same
  commit**. Same trap, same function, same fresh-venv precondition, opposite outcome.
- Commit `c10535f` (2026-07-24) hit it a third time with `git` in `Reset-Lockfile` and
  invented the `$ErrorActionPreference = "Continue"` drop, documenting it in a comment
  local to that helper.

Three encounters, three different ad-hoc remedies (`try/catch`, an EAP drop, and a
redirect that made it worse), and no rule anywhere saying which to reach for. The
`try/catch` framing is the reason the third one slipped: it reads as "wrap risky calls",
which does not tell you that adding a redirect to a call you were not worried about is
itself the hazard.

### Generalizable rule

- **In a PowerShell script with `$ErrorActionPreference = "Stop"`, adding `2>$null` or
  `2>&1` to a native command makes it *more* likely to abort the script, not less.** Flag
  any redirect added to silence a native command's output. If the goal is to ignore
  stderr, drop the preference around the call (`Continue`, restored in a `finally`) or
  wrap it in `try/catch` — the redirect alone cannot do it.
- **A tool that emits warnings on stderr during normal operation cannot be called
  unprotected from such a script.** `pip`, `git`, and `npm` all do. This is not about the
  command failing; `pip uninstall` here exited 0.
- **When a step exists to handle "the resource might already be there", check what the
  tool says when it is *not* there** — that branch is the one a fresh install always
  takes, and the one no existing install ever will. Cleanup and uninstall steps are where
  this inverts.
- **A bash `|| true` does not survive a port to PowerShell.** `manage.sh` has never had
  this bug because line 401 reads `... --quiet 2>/dev/null || true`; the `|| true` is
  doing all the work and has no PowerShell equivalent. When mirroring a shell line into
  `manage.ps1`, port the error *handling*, not just the command.

### Why it wasn't caught the first time

Nothing runs `manage.ps1` on a fresh Windows venv except a user installing from scratch.
CI is Linux and never touches the PowerShell launcher; every developer machine already had
torch, which is precisely the state that hides this. The `update` path — the one that does
get exercised — takes the opposite branch.

The reviewable signal was there, though: the same commit added a `try/catch` for this exact
trap, so "does anything else in this diff talk to stderr under `Stop`?" would have caught
it. That question is now the rule above.

### Fix

Both pip calls moved inside an `$ErrorActionPreference = "Continue"` block restored in a
`finally`, and the `2>$null` removed. The install joined the block because a routine pip
warning there would abort identically, and its real outcome is judged by the
`torch.cuda.is_available()` probe below rather than by anything the preference affects.
Behaviour is otherwise unchanged — still uninstall-first, still verified against the
interpreter. Documented in `docs/dev/environment-setup.md` § PyTorch GPU auto-detection,
next to the `$hasCuda` `try/catch` note describing the same trap.

The sweep the rule implies was then run over the rest of `manage.ps1`, and three more
redirected-and-unguarded native calls got the same treatment. All three are **probes whose
failure the surrounding code already handles**, so the abort was pre-empting a recovery
path that was written and correct:

- `py -3.12 -c "import sys; print(sys.executable)"` in `Install-Deps`, reached right after
  winget installs Python. The launcher prints `Python 3.12 not found!` to stderr when it
  has not yet picked up that install — exactly the case the `$LASTEXITCODE` check and the
  `Get-Command python` fallback two lines down exist for.
- `venv\Scripts\python.exe --version` in `Cmd-Setup`, whose entire purpose is to detect a
  venv that must be deleted and rebuilt. An interpreter that is *broken* rather than merely
  outdated — relocated repo, partly-deleted `Scripts\` — writes to stderr, and the abort
  landed before the recreate branch that would have repaired it. A null result now matches
  no version and falls to that same branch, which is the right answer.
- The equivalent probe in `Cmd-Update`.

Left alone deliberately: the two `node --version 2>&1` calls, which are guarded by
`Get-Command` and whose output goes to stdout. The one at `manage.ps1:121` can still throw
`CommandNotFoundException` when winget's Node install is not yet on `PATH`, but that is a
different mechanism with a different fix and no reported occurrence.

### Status & date

MITIGATED. Last reviewed for staleness: 2026-07-31.
