from pydantic import BaseModel, Field, model_validator


class InpaintRunRequest(BaseModel):
    """Scope + options for `POST /inpaint/run` (the `batch_inpaint` job).

    `labels` and `label` are different things and the names are deliberate — they
    match every sibling request model. `labels` filters which *detections* become
    the paint mask; `label` overrides the job's own display name.
    """

    dataset_id: str
    image_ids: list[str] | None = None       # None = dataset scope
    subfolder: str | None = None
    quality_flags: list[str] | None = None   # exclude images with these flags set
    labels: list[str] | None = None          # detection labels to paint out; None/[] = all
    # Grow the rasterized mask by this many pixels before painting. Segmentation
    # boundaries sit tight against a semi-transparent watermark and leave a halo
    # of its edge pixels behind; a few px of dilation swallows that. Measured
    # against a synthetic watermark, 6 costs nothing and 12 starts eating
    # neighbouring detail, so the default is at the low end.
    dilate_px: int = Field(6, ge=0, le=64)
    replace: bool = False
    dest_subfolder: str | None = None        # new-file mode: subfolder for the new image; None = same as source
    label: str | None = None                 # job-label override (NOT a detection label)

    @model_validator(mode="after")
    def _dest_subfolder_is_new_file_only(self):
        """Refuse `replace` + `dest_subfolder` — the pair has no meaning.

        Replace mode overwrites the source in place, so there is no new file for a
        destination subfolder to place; silently ignoring the field would leave a
        caller believing it had moved something. A 422 rather than a 400 because
        the request is malformed, not the state. The UI never sends the pair.
        """
        if self.replace and self.dest_subfolder is not None:
            raise ValueError(
                "dest_subfolder is only meaningful in new-file mode (replace=false)"
            )
        return self
