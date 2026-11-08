# Multiple installs

**Status: living.** Running more than one Crucible install, on one machine or several: the
ports, the rule that keeps two installs from deleting each other's datasets, keeping
datasets on a network share, moving a database to another machine, and running an install
as a service. The examples below are made up; a maintainer's own machine notes, if any,
are kept out of the repo.

## Two installs on one machine

Give each its own clone, its own `.env` and its own port: `CRUCIBLE_PORT` for the backend,
`CRUCIBLE_VITE_PORT` for `dev`'s Vite server (`docs/dev/server-start.md`). For example, a
`~/crucible-main` on 8000 and a `~/crucible-test` on 8100 with Vite on 5200. Each needs its
own database (`DATABASE_URL`, by default `dataset_manager.db` in the clone) and its own
datasets folder. Different ports are also different browser origins, so the two keep apart
their browser storage too.

## Never share a datasets folder

**Never point two installs at one datasets folder**, nor at a folder that holds anything
else. At every start, an install moves each folder in its datasets folder that its own
database does not list into `.orphaned/` there (the orphan sweep, `docs/dev/database.md`).
The sweep refuses to run in the cases it can recognise (a row outside the folder, rows whose
folders are all missing, an empty database beside dataset folders), but a second install
that has datasets of its own looks like none of those, and would move the first one's aside
at each start, breaking every one of them until moved back by hand. So:

- each install gets its own datasets folder, e.g. `/mnt/nas/crucible-main/datasets`;
- exports go beside it (`/mnt/nas/crucible-main/exports`), never inside it;
- to use a dataset in another install, import a copy of its `images` folder there.

## Datasets on a network share

`DATASETS_DIR` can name a mounted share (SMB/CIFS, NFS); the database stays on local disk.
On Linux, mount it through fstab with `x-systemd.automount` and `nofail`, so the machine
boots while the file server is off. Then make the service refuse to start until the share
is really there: a plain `test -d` on an automount point triggers a mount attempt and can
pass on an empty mount point, so test for a marker **file** you put at the share's root, e.g.
`ExecStartPre=/usr/bin/test -f /mnt/nas/crucible-main/.crucible-share`. A file, not a
folder, so the sweep never touches it. A cifs sharing violation reaches Crucible as
`EBUSY`/`EACCES`, which the locked-file retries already handle
(`docs/dev/shared-utilities.md`).

## Moving a database to another machine

Every stored file path is absolute, written in the old machine's form (a Windows install
writes backslashes). Rewrite them **before** the first start on the new machine:
`datasets.folder_path`, `images.file_path`/`thumbnail_path`, `videos.file_path`/`poster_path`
and `version_image_states.file_path`, swapping the old datasets-folder prefix for the new
one. Until then the sweep refuses to run, so nothing is deleted, but every dataset points
at folders that do not exist. With `PRAGMA foreign_keys=ON`, deleting a `datasets` row
cascades to everything keyed to it except `background_jobs`, which keeps a NULL
`dataset_id`. Machine-specific settings travel too: `threshold_settings.comfyui_url` and
`comfy_workflow_dir`, the providers' base URLs, and export folders in `run_recipes` and
`threshold_settings.run_form`.

A branch's migrations are one-way: a database migrated by an unmerged branch cannot step
back to `main` (`docs/dev/database.md`). Back it up before updating such an install.

## Running as a service

`manage.sh start` can be a systemd user service's `ExecStart`, with
`Environment=CRUCIBLE_NO_BROWSER=1` and `Restart=on-failure`. The script's own loop keeps
the in-app Restart button working; Shutdown exits it cleanly, so systemd does not restart
it. A user service needs `loginctl enable-linger <user>` to run without a login. To update:
back up the database, `git pull`, then `systemctl --user restart <service>`; `start` runs
the migrations and rebuilds the frontend itself.

A small GPU shared by ComfyUI and a local LLM server is
`docs/dev/comfyui/gpu-handoff.md`.
