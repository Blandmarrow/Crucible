# DINOv3 runs — raw material

The evidence behind § The DINOv3 run and § The seven-illustrator run in
`../style-embedding.md`. Quote numbers from these files, not from memory.

- `test2/`: two anime styles (Highlander, Hellsing Ultimate) in the dev DB.
  - `PREREGISTRATION.md`
  - `sweep.csv` and `norm_sweep.csv`: the original and the format-normalised files.
  - `rule_a*.txt` and `rule_b.txt`: the two rules.
  - `format_control.txt`, `crossover.txt` and `review_checks.txt`: the checks the opus
    review asked for.
- `test3/`: seven public-domain illustrators.
  - `PREREGISTRATION.md`, `rules.txt`, `rule_b.txt`, `sweep.csv` and `groups.json`.
  - `MANIFEST.json`: every image's Commons page and licence.
  - `exclude.json`: what the contact-sheet pass removed, and why.
- `*/extract_all.sh` and `*/probe_all.sh`: the run order. Set `R` to an output folder first.
  They expect the dataset in the dev DB and the pool folders to exist.

The scripts are `backend/scripts/style_research/`:
- `fetch_commons.py`, `screen.py` and `finalize.py` build a test set from Wikimedia Commons.
- `dinov3_sweep.py` is the separability sweep.

Run the sweep from the repo root with `PYTHONPATH` set to it.
