"""Curated license vocabulary + provenance inheritance resolution.

The single source of truth for what license ids exist and what they permit.
`frontend/src/constants/licenses.ts` mirrors this list — keep the two in sync
(`backend/tests/test_provenance.py::test_frontend_license_vocabulary_matches_backend`
parses that file and enforces the match).

Deliberately not a plain free-text field: `license` is filtered and grouped on
(gallery filter, export filters, stats breakdown), so it needs a closed
vocabulary. The `other:<free text>` escape hatch covers everything else without
polluting the aggregate buckets.

Import the vocabulary and the provenance helpers from here: never hardcode a
license id list, and never re-inline the inheritance coalesce that
`resolve_provenance` performs. `resolve_provenance` is duck-typed and must not
import models, or it re-creates an import cycle.

Ingest truncates; the API rejects. An import must never fail on a bad sidecar
(`merge_provenance`/`clamp_provenance` clamp to `FIELD_MAX_LEN`), while an API
client must never silently lose data (`normalize_license_input`, the Pydantic
validator, normalizes *then* length-checks and raises). See
`docs/dev/provenance.md` for the full write-side rules.
"""

import copy
from dataclasses import dataclass

OTHER_PREFIX = "other:"


@dataclass(frozen=True)
class LicenseInfo:
    id: str
    label: str
    allows_commercial: bool | None  # None = unknown / unverifiable
    requires_attribution: bool
    share_alike: bool
    url: str = ""
    # Redistribution of a modified version is not permitted. Surfaced in CREDITS.md
    # and behind the export's "Exclude no-derivatives" filter, because a training
    # dataset ships resized/cropped copies — which is exactly what ND forbids.
    no_derivatives: bool = False


_ALL: tuple[LicenseInfo, ...] = (
    LicenseInfo("unknown", "Unknown", None, False, False),
    LicenseInfo("owned", "Owned / self-created", True, False, False),
    LicenseInfo("public-domain", "Public domain", True, False, False),
    LicenseInfo("CC0-1.0", "CC0 1.0 (no rights reserved)", True, False, False,
                "https://creativecommons.org/publicdomain/zero/1.0/"),
    LicenseInfo("CC-BY-4.0", "CC BY 4.0", True, True, False,
                "https://creativecommons.org/licenses/by/4.0/"),
    LicenseInfo("CC-BY-SA-4.0", "CC BY-SA 4.0", True, True, True,
                "https://creativecommons.org/licenses/by-sa/4.0/"),
    LicenseInfo("CC-BY-NC-4.0", "CC BY-NC 4.0", False, True, False,
                "https://creativecommons.org/licenses/by-nc/4.0/"),
    LicenseInfo("CC-BY-NC-SA-4.0", "CC BY-NC-SA 4.0", False, True, True,
                "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    LicenseInfo("CC-BY-ND-4.0", "CC BY-ND 4.0", True, True, False,
                "https://creativecommons.org/licenses/by-nd/4.0/", no_derivatives=True),
    LicenseInfo("licensed-commercial", "Licensed for commercial use", True, False, False),
    LicenseInfo("research-only", "Research / non-commercial only", False, True, False),
    LicenseInfo("synthetic", "Synthetic (AI-generated)", True, False, False),
    # A *recorded* answer, not a missing one: the source explicitly grants nothing.
    # Distinct from "" ("no license recorded") on purpose — as "" it would be
    # dropped at normalization and the image would inherit the dataset's default,
    # so a scrape that declares "all rights reserved" could ship as CC-BY.
    LicenseInfo("no-license", "No license granted", False, False, False),
)

LICENSES: dict[str, LicenseInfo] = {li.id: li for li in _ALL}
LICENSE_IDS: frozenset[str] = frozenset(LICENSES)

# Shown when the value is empty, unrecognised, or an `other:` free-text string.
_UNKNOWN = LICENSES["unknown"]

# Case-insensitive lookup for sidecar values that use different capitalisation
# ("cc-by-4.0", "CC0-1.0", "cc0"). Sidecars are not authored by us.
_BY_LOWER: dict[str, str] = {lid.lower(): lid for lid in LICENSES}
# Deliberately no version-less Creative Commons tokens ("by", "cc-by", "by-nc",
# …). An Openverse payload carries `"license": "by"` with the version in a
# *separate* field, so mapping it to `CC-BY-4.0` invents a version nobody stated
# and makes CREDITS.md link the 4.0 deed for what may be a 2.0 image. Without an
# alias they fall through to `other:<raw>`: the license is still recorded, just
# not upgraded to a claim the source never made. `cc0` is safe (one version
# exists); every versioned long form is already covered by `_BY_LOWER`.
_ALIASES: dict[str, str] = {
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "publicdomain": "public-domain",
    "public domain": "public-domain",
    "pd": "public-domain",
    "none": "no-license",
    "no license": "no-license",
    "all rights reserved": "no-license",
    "copyright": "no-license",
}


def normalize_license(v: str | None) -> str:
    """Normalise an arbitrary license string to "" | a known id | "other:<free text>".

    Empty/whitespace → "". A known id (any capitalisation) or a known alias →
    the canonical id. An existing `other:` value is kept verbatim (trimmed).
    Anything else is preserved as `other:<raw>` rather than dropped — losing the
    only record of a license is worse than an unrecognised bucket.
    """
    s = (v or "").strip()
    if not s:
        return ""
    if s.lower().startswith(OTHER_PREFIX):
        body = s[len(OTHER_PREFIX):].strip()
        return f"{OTHER_PREFIX}{body}" if body else ""
    low = s.lower()
    if low in _BY_LOWER:
        return _BY_LOWER[low]
    if low in _ALIASES:
        return _ALIASES[low]
    return f"{OTHER_PREFIX}{s}"


def normalize_license_input(v: str | None) -> str | None:
    """Normalize a client-supplied license id and enforce the column width.

    Must run as a Pydantic **validator**, never as a `max_length` constraint:
    `normalize_license` prepends the 6-char `other:` prefix *after* a `max_length`
    check would already have passed, so a 64-char free-text license validates and
    then stores 70 characters. That value is over the column width, so reading the
    row back fails the response schema — which is how an image's provenance
    becomes permanently uneditable (422 on every save).

    Rejects rather than truncates: this is the API direction. Ingest capture
    truncates instead, via `clamp_provenance`.
    """
    if v is None:
        return None
    normalized = normalize_license(v)
    limit = FIELD_MAX_LEN["license"]
    if len(normalized) > limit:
        raise ValueError(
            f"license is too long: {len(normalized)} characters after normalization "
            f"(the 'other:' prefix counts), maximum {limit}"
        )
    return normalized


def license_info(v: str | None) -> LicenseInfo:
    """LicenseInfo for a stored value; unknown-shaped fallback for "" and `other:`.

    Callers that need the raw free text of an `other:` value should read the
    stored string — this returns a permission-conservative descriptor (commercial
    use unknown) so filters never assume more rights than are recorded.
    """
    s = (v or "").strip()
    if not s:
        return _UNKNOWN
    if s in LICENSES:
        return LICENSES[s]
    if s.lower().startswith(OTHER_PREFIX):
        body = s[len(OTHER_PREFIX):].strip()
        return LicenseInfo(s, body or "Other", None, False, False)
    return _UNKNOWN


def license_label(v: str | None) -> str:
    """Human-readable label for a stored license value."""
    return license_info(v).label


def allows_commercial(v: str | None) -> bool:
    """True only when the license is *known* to permit commercial use.

    Unknown (None) is treated as "no" — a commercial-use export filter must not
    let through images whose rights were never established.
    """
    return license_info(v).allows_commercial is True


PROVENANCE_FIELDS = ("source_name", "source_url", "license", "attribution")
# The Image/VersionImageState column set — the four inheritable strings plus the
# image-only raw payload.
IMAGE_PROVENANCE_FIELDS = (*PROVENANCE_FIELDS, "source_meta")

# Per-field caps, matching the Image/Dataset column widths. `attribution` is a
# TEXT column with no width of its own, so the cap here is the only bound on it —
# generous, because a credit line can legitimately name several rights holders.
# `license` is capped *after* normalization, since `normalize_license` prepends
# the 6-char `other:` prefix.
FIELD_MAX_LEN: dict[str, int] = {
    "source_name": 255,
    "source_url": 1024,
    "license": 64,
    "attribution": 2000,
}


def clamp_provenance(values: dict) -> dict:
    """Truncate captured provenance strings to their column widths.

    Ingest **truncates** where the API **rejects**: an import must not fail on one
    over-long sidecar value, but a value longer than its column also must not be
    written — SQLite does not enforce `String(n)`, so an over-long value stores
    fine and then makes that image's provenance permanently unsaveable, since the
    edit endpoint validates what it reads back.

    Returns a new dict; non-string values (`source_meta`) pass through untouched.
    """
    out = dict(values)
    for field, limit in FIELD_MAX_LEN.items():
        value = out.get(field)
        if isinstance(value, str) and len(value) > limit:
            out[field] = value[:limit].rstrip()
    return out


def merge_provenance(*layers: dict | None, fields: tuple[str, ...] = IMAGE_PROVENANCE_FIELDS) -> dict:
    """Merge provenance dicts with left-to-right precedence, keeping only real values.

    Used at ingest to layer request-supplied fields over sidecar-derived over
    EXIF-derived. Fields absent from every layer are simply omitted, so the
    resulting `Image(**merged)` leaves them NULL and they inherit the dataset
    default.

    The single ingest choke point, so the result is `clamp_provenance`d here and
    every capture path (import, rescan, upload) is covered by one cap.

    `fields` narrows the column set for a model that does not carry all of them:
    video ingest passes `PROVENANCE_FIELDS`, because `Video` has the four
    inheritable strings but no `source_meta`, and a layer carrying that key would
    otherwise make `Video(**merged)` raise TypeError.
    """
    out: dict = {}
    for layer in layers:
        if not layer:
            continue
        for field in fields:
            if not out.get(field) and layer.get(field):
                out[field] = layer[field]
    return clamp_provenance(out)


def copy_provenance(img) -> dict:
    """The raw provenance columns of an image, for a derived copy in the same dataset.

    Raw — not resolved — so an inherited value stays inherited and keeps tracking
    the dataset default. Use `resolve_provenance` instead when the copy lands in a
    *different* dataset, where inheritance would silently re-point at an unrelated
    default.

    `source_meta` is deep-copied, not aliased: returning the parent's dict would
    leave parent, derivative and every snapshot of either sharing one mutable JSON
    object — the exact shape of the "never mutate a loaded JSON column in place"
    invariant, waiting for the first caller that edits one.
    """
    out = {f: getattr(img, f, None) for f in IMAGE_PROVENANCE_FIELDS}
    if out.get("source_meta") is not None:
        out["source_meta"] = copy.deepcopy(out["source_meta"])
    return out


def materialize_provenance(img, ds) -> dict:
    """Concrete provenance columns for a copy/move into a *different* dataset.

    Resolves inheritance against the source dataset and writes the result as real
    values, so the image keeps its own license instead of picking up the
    destination dataset's unrelated default.
    """
    resolved = resolve_provenance(img, ds)
    out = {f: (resolved.get(f) or None) for f in PROVENANCE_FIELDS}
    out["source_meta"] = getattr(img, "source_meta", None)
    return out


def materialize_by_source(rows, ds_by_id: dict) -> dict[str, dict]:
    """`materialize_provenance` per row, each against **its own** source dataset.

    A selection can span datasets (the gallery toolbar shows a per-dataset
    breakdown precisely because of that), so resolving every row against one
    dataset would stamp that dataset's defaults onto unrelated images — and
    materialized values are concrete, so the mistake is not recoverable.

    `rows` need a `.id` and a `.dataset_id`; `ds_by_id` maps dataset id to the
    dataset (a missing entry resolves against None, i.e. no inheritance).
    Returns `{image_id: provenance columns}`.
    """
    return {
        row.id: materialize_provenance(row, ds_by_id.get(row.dataset_id))
        for row in rows
    }


def resolve_provenance(img, ds) -> dict:
    """Merge image-level provenance over dataset-level defaults.

    A NULL/empty image field means "inherit the dataset default" — this is the
    read-time half of that rule. Both arguments are duck-typed (ORM rows,
    `select(...)` result rows, or any object with the attributes), so this module
    stays free of model imports and therefore of import cycles. Either may be
    None.

    Returns the four resolved strings plus `source_meta` (image-only, never
    inherited) and `inherited`: the subset of field names that came from the
    dataset, so the UI can show "inherited" vs "overridden".
    """
    out: dict = {}
    inherited: list[str] = []
    for field in PROVENANCE_FIELDS:
        own = (getattr(img, field, None) or "") if img is not None else ""
        if own:
            out[field] = own
        else:
            fallback = (getattr(ds, field, None) or "") if ds is not None else ""
            out[field] = fallback
            if fallback:
                inherited.append(field)
    out["source_meta"] = getattr(img, "source_meta", None) if img is not None else None
    out["inherited"] = inherited
    return out
