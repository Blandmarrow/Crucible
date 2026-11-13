# M5 — App-stored prompts

**Status: archive (landed 2026-10-30).** *Record for the M5 line in the
[roadmap](../roadmap.md#landed). Why the work was done:
[decision 3](../decisions/03-app-stored-prompts.md).* Merged as PR #101. Archived on
2026-11-13, when only its cleanup was left; that cleanup stays open in the roadmap's
§ Waiting until a release has passed.

Saved prompts moved from the browser to the database, merged on 2026-10-30 as PR #101. Neither
this nor M4 depends on the other. Gate: the cleanup below done, once browsers have had a release
to copy their data over. Design: [server-state-plan/README.md](../server-state-plan/README.md). Ruling:
[decision 3](../decisions/03-app-stored-prompts.md).

- [x] Stage 1: backend models, migration, routes and the import endpoint.
- [x] Stage 2: caption prompt presets. Needs stage 1.
- [x] Stage 3: ComfyUI "Generate prompts" settings, and the conflict dialog. Needs stage 2.
- [x] Stage 4: the captioning workflow blob. Needs stage 3, for the writer and the loading gate.
- [x] Stage 5: declared categories. Needs stage 2.
- [ ] Cleanup, one release after PR #101: delete the legacy keys, the import code and the plan
  folder, then archive this milestone.
