"""Pure helpers for the ComfyUI prompt library (M6 §8c): category paths, and the
arithmetic behind re-filing a whole category subtree.

A library category is a `/`-separated path shown as a tree (`portraits/studio`). It is a
label, never a filesystem path, so it is normalized by its own rule rather than by
`utils.normalize_subfolder`: each segment is **trimmed** (a folder keeps its spaces, a
category label should not grow a twin `studio ` beside `studio`), and `..` has no special
meaning, since nothing is ever resolved against it.

Subtree tests are exact, case-sensitive prefix compares. Never `LIKE`: SQLite's `LIKE`
ignores ASCII case, which is how a §8b folder delete of `knight` took `Knight/a` with it.
The SQL twin of `in_category` is `category_subtree_clause` in `routers/comfy.py`.
"""
from collections.abc import Iterable

# The request models' limit. SQLite does not enforce `String(n)`, so Pydantic is the
# real bound; the column is widened to match so the model and the schema agree.
CATEGORY_MAX_LEN = 512


def dedupe_key(text: str) -> str:
    """The library's duplicate rule: case- and whitespace-insensitive, per category."""
    return " ".join(text.split()).lower()


def normalize_category(raw: str) -> str:
    """Fold `\\` to `/`, trim every segment, drop blank and `.` segments, re-join.

    Returns `""` for a category with no segment left; callers refuse that (a prompt
    always sits in some category). The migration `e7d1c5a9b3f2` inlines this same rule
    to normalize the categories saved before it, so keep the two in step.
    """
    parts = [p.strip() for p in raw.replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p and p != ".")


def in_category(category: str, path: str) -> bool:
    """True when `category` is `path` or sits anywhere below it."""
    return category == path or category.startswith(path + "/")


def remap_category(category: str, path: str, new_path: str) -> str:
    """`category`'s name once the subtree at `path` is re-filed as `new_path`."""
    return new_path + category[len(path):]


def plan_category_move(
    rows: Iterable[tuple[str, str, str]], path: str, new_path: str
) -> tuple[dict[str, str], list[str]]:
    """Plan re-filing the category subtree `path` as `new_path`.

    `rows` are `(id, category, text)` for every prompt in either subtree, in the order
    that should win a collision (oldest first). Prompts outside `path`'s subtree are the
    destination's occupants and stay put. A moved prompt whose text already exists in its
    new category — an occupant, or an earlier moved prompt — is merged away, the same rule
    `library/move` applies to single prompts.

    Returns `(moves, merges)`: `{id: new_category}` for prompts that change category, and
    the ids to delete. The caller has refused `new_path` inside `path` already.
    """
    rows = list(rows)
    seen = {
        (cat, dedupe_key(text)) for _id, cat, text in rows if not in_category(cat, path)
    }
    moves: dict[str, str] = {}
    merges: list[str] = []
    for row_id, cat, text in rows:
        if not in_category(cat, path):
            continue
        target = remap_category(cat, path, new_path)
        key = (target, dedupe_key(text))
        if key in seen:
            merges.append(row_id)
            continue
        seen.add(key)
        if target != cat:
            moves[row_id] = target
    return moves, merges
