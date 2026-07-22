"""Curated license vocabulary + provenance inheritance resolution.

The single source of truth for what license ids exist and what they permit.
`frontend/src/constants/licenses.ts` mirrors this list — keep the two in sync
(the `GET /api/v1/licenses` endpoint serves this module so the UI can verify).

Deliberately not a plain free-text field: `license` is filtered and grouped on
(gallery filter, export filters, stats breakdown), so it needs a closed
vocabulary. The `other:<free text>` escape hatch covers everything else without
polluting the aggregate buckets.
"""

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
                "https://creativecommons.org/licenses/by-nd/4.0/"),
    LicenseInfo("licensed-commercial", "Licensed for commercial use", True, False, False),
    LicenseInfo("research-only", "Research / non-commercial only", False, True, False),
    LicenseInfo("synthetic", "Synthetic (AI-generated)", True, False, False),
)

LICENSES: dict[str, LicenseInfo] = {li.id: li for li in _ALL}
LICENSE_IDS: frozenset[str] = frozenset(LICENSES)

# Shown when the value is empty, unrecognised, or an `other:` free-text string.
_UNKNOWN = LICENSES["unknown"]

# Case-insensitive lookup for sidecar values that use different capitalisation
# ("cc-by-4.0", "CC0-1.0", "cc0"). Sidecars are not authored by us.
_BY_LOWER: dict[str, str] = {lid.lower(): lid for lid in LICENSES}
_ALIASES: dict[str, str] = {
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc-by": "CC-BY-4.0",
    "cc-by-4.0": "CC-BY-4.0",
    "by": "CC-BY-4.0",
    "by-sa": "CC-BY-SA-4.0",
    "by-nc": "CC-BY-NC-4.0",
    "by-nc-sa": "CC-BY-NC-SA-4.0",
    "by-nd": "CC-BY-ND-4.0",
    "publicdomain": "public-domain",
    "public domain": "public-domain",
    "pd": "public-domain",
    "none": "",
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


def merge_provenance(*layers: dict | None) -> dict:
    """Merge provenance dicts with left-to-right precedence, keeping only real values.

    Used at ingest to layer request-supplied fields over sidecar-derived over
    EXIF-derived. Fields absent from every layer are simply omitted, so the
    resulting `Image(**merged)` leaves them NULL and they inherit the dataset
    default.
    """
    out: dict = {}
    for layer in layers:
        if not layer:
            continue
        for field in IMAGE_PROVENANCE_FIELDS:
            if not out.get(field) and layer.get(field):
                out[field] = layer[field]
    return out


def copy_provenance(img) -> dict:
    """The raw provenance columns of an image, for a derived copy in the same dataset.

    Raw — not resolved — so an inherited value stays inherited and keeps tracking
    the dataset default. Use `resolve_provenance` instead when the copy lands in a
    *different* dataset, where inheritance would silently re-point at an unrelated
    default.
    """
    return {f: getattr(img, f, None) for f in IMAGE_PROVENANCE_FIELDS}


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
