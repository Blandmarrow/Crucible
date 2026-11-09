# Decision 14: Job throughput is its own milestone

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-09.
- **Status:** **Decided** (2026-11-09, by the user) that job throughput is milestone M10,
  after M9 and before M7. **Leaning** on how it is built: CPU-only work first, the decode
  change only after a timing, and no batching. Each is safe to build around, and the user
  can still overrule any one of them.
- **Decision:** the throughput findings of the 2026-11-09 review become milestone M10, with
  its own plan, [throughput-plan/README.md](../throughput-plan/README.md). M9 keeps first
  place ([decision 13](13-scale-work-first.md)); M10 goes between it and M7, and the other
  open milestones keep their own order. Within M10 the CPU-only stages go first: the
  technical scorer on several cores, concurrent requests for the remote captioners, and a
  parallel import. The GPU scorers change only after a timing on a real card shows that
  image decoding is a large share of a run.
- **Rationale:**
  - Every long job handles one image at a time and awaits it, even where the work is
    CPU-only and cores sit idle. The technical scorer, the remote captioners and import
    never touch the GPU, so parallel work there carries no GPU risk.
  - The work shares no file with M9, so it is not a stage of the scale plan. It is a
    different question: M9 is about what breaks or is lost at size, this is about how long a
    job takes at any size.
  - PM-035's one-GPU-thread rule is not reopened. The plan moves CPU work only, which the
    rule already allows, and the guard test stays as it is.
  - Nothing has been timed yet, which is why the GPU-path change waits for a measurement
    and the CPU-only stages each start with one.
- **Rejected alternatives:**
  - **Fold it into M9.** M9 already gained three stages on 2026-11-09, and its gate is
    about scale and data safety. Throughput would blur that gate.
  - **Leave it as a note with no milestone.** The findings are concrete and three of them
    are low-risk; a note with no place in the order is how the paid-call cap bug sat
    unticked in prose.
  - **Batched forward passes.** Several images in one model call may move a score in its
    last decimal place. That is not measured here; the nearest evidence is M9 stage 5, where
    float32 rounding moved stored style scores by 0.0001 when only the row order changed.
    The owner rejects speedups that change output, so batching is not built. Reopening it
    needs a measurement and a superseding entry.
  - **More GPU threads, or GPU calls back on the default pool.** That is the leak PM-035
    fixed.
  - **Running it after M7 and M8.** The user put it directly after M9.
- **How it's verified:** each stage's acceptance in the plan compares its output with the
  serial path's on a fixture, and the GPU-path stage repeats PM-035's VRAM hand check.
