"""Pure row-order arithmetic for the ComfyUI queue (M6 §8c).

A plan's run order is `ComfyRow.sort_order`, ties broken by `created_at`. It decides more
than the order rows run in: which rows survive a run's image-cap clamp, Top up's pending
order, and the `increment` seed a row gets (its index within the run). The table groups
rows by folder, so the order across folders is not what the table shows; within a folder
it is.
"""
from collections.abc import Sequence
from typing import Literal


def place_rows(
    order: Sequence[str],
    moving: Sequence[str],
    anchor: str | None = None,
    position: Literal["before", "after"] = "before",
    end_after: str | None = None,
) -> list[str]:
    """The plan's new row order once `moving` is placed as one block.

    `order` is every row id in the current run order. The moved rows keep their order
    relative to each other, taken from `order`, not from `moving`. They land before or
    after `anchor`; with no anchor, right after `end_after` (the last row of the target
    folder that is not moving); with neither, at the very end.

    Raises `ValueError` if `anchor` is one of the moving rows: the caller picks the nearest
    row that stays put instead.
    """
    mv = set(moving)
    if anchor is not None and anchor in mv:
        raise ValueError("the anchor row cannot be one of the rows being placed")
    rest = [i for i in order if i not in mv]
    block = [i for i in order if i in mv]
    if anchor is not None:
        k = rest.index(anchor) + (1 if position == "after" else 0)
    elif end_after is not None and end_after in rest:
        k = rest.index(end_after) + 1
    else:
        k = len(rest)
    return rest[:k] + block + rest[k:]
