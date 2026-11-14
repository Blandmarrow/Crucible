# Third-party notices

Crucible's own licence is in `LICENSE`. This file credits third-party data the app can
download and use.

## Open English WordNet (synonym suggestions)

When you turn on **Settings → UI Behavior → Synonym suggestions**, Crucible downloads
**Open English WordNet 2025** (core edition) from the project's GitHub release
(`globalwordnet/english-wordnet`, tag `2025-edition`) and converts it on your machine.
Crucible's repository and releases contain none of the dictionary data.

- **Open English WordNet** is copyright (c) 2019-present The Open English WordNet Team,
  licensed under the [Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/).
  Website: <https://en-word.net/>.
- It is derived from **Princeton WordNet**, under the WordNet licence reproduced below.
- **What Crucible changes:** it keeps only each meaning's words, part of speech, first
  definition and "similar" links, plus each word's senses and irregular forms, and stores
  them in one SQLite file. Nothing else is altered.
- The installed copy (`data/dictionary/`) keeps the full `LICENSE.md` and
  `WNDB_License.txt` from that release beside the converted file, plus a `NOTICE.txt`
  describing the conversion. The same two files are in this repository under
  `backend/resources/wordnet/`.

The WordNet licence, as shipped with the release:

```text
This software and database is being provided to you, the LICENSEE, by
the Open English Wordnet team under the Creative Commons Attribution 4.0
International License (CC-BY 4.0).

Open English Wordnet 2023 Copyright 2023 by the Open English Wordnet team.

Permission to use, copy, modify and distribute this software and
database and its documentation for any purpose and without fee or
royalty is hereby granted, provided that you agree to comply with
the following copyright notice and statements, including the disclaimer,
and that the same appear on ALL copies of the software, database and
documentation, including modifications that you make for internal
use or for distribution.

WordNet 3.1 Copyright 2011 by Princeton University.  All rights reserved.

THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PRINCETON
UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF MERCHANT-
ABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE
OF THE LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT
INFRINGE ANY THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR
OTHER RIGHTS.

The name of Princeton University or Princeton may not be used in
advertising or publicity pertaining to distribution of the software
and/or database.  Title to copyright in this software, database and
any associated documentation shall at all times remain with
Princeton University and LICENSEE agrees to preserve same.
```
