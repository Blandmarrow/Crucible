"""The app's version: the one place it is written down.

Semantic versioning, three numbers, by decision 19 (docs/dev/decisions/19-release-versioning.md):
PATCH for fixes only, MINOR for new features, MAJOR when updating needs a manual step beyond
`manage update`. The `release` skill bumps this line in the release PR, and the GitHub tag is
`v` plus this value.
"""

__version__ = "1.4.0"
