# Decision 6: Layer-7 default, and no new style model

*Index: [decisions.md](../decisions.md).*

- **Date:** 2026-11-03.
- **Status:** **Decided** (2026-11-03, by the user).
- **Decision:** M7 ports the DINOv2 layer-7 style default from `style-embedding-measurement`
  in the next session. None of the candidate style models (Kaloscope 3.0, CSD, PE-Core) is
  adopted for now.
- **Rationale:** the layer-7 baseline beat the app on `main` on all five sets of the two
  pre-registered runs: anime, illustrators, FSA photographs, digital paintings and 3D films.
  Those runs do not separate the layer from the blend weight on the new media; the earlier
  run did, and there the layer was nearly all of the gain. No
  candidate did better across media. Kaloscope led widely on anime and illustration but tied
  films and photographs, and its verdict there is fragile; CSD stayed under the bar
  everywhere outside drawn art; PE-Core's only gain, on films, sat at the bar and is not
  separated from content matching. In the user's words, there are no better alternatives
  available currently. The evidence is summarised in
  `docs/dev/quality/style-evaluation.md` § Other models were measured and not adopted (until
  the M7 port, in a `style-model-research/` folder since deleted).
- **Rejected alternatives:** adopting Kaloscope (the strongest on drawn art, but a restrictive
  licence and no general gain) or CSD (the permissive fallback, with a small and uneven gain)
  as a style mode now.
- **Open, not decided here:** a model can be reconsidered later through a new superseding
  entry, for example as an optional anime mode. The CLIP QuickGELU fix is a separate item on
  the roadmap.
