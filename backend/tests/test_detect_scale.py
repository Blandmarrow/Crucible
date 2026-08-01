"""`_detect_scale` — the filename heuristic behind the model dropdown badge and
the New-file output suffix.

Nothing about inference depends on this: spandrel auto-detects the architecture
and the real factor comes from `descriptor.scale`. The heuristic only decides
what the UI says and what a New-file run names its output, so a miss is cosmetic
— but it missed the *dominant* openmodeldb convention, which puts no separator
after the `Nx` token (`4xNomos8kSCHAT-L`) and uses scale 1 for restoration
models (`1xDeJPG_SRFormer_light`) that the old `[2-8]` class could not express.

The leading boundary is deliberately strict, and the negative cases below are
what it buys: relaxing it to match `HAT-L_SRx4_ImageNet-pretrain` would also
make `Box4` read as a 4x model.

`backend/ml/upscaler.py` is torch-free at import time (`backend/ml/device.py`
imports torch lazily inside each function), so this module needs neither
`needs_torch` nor `needs_cv2` and runs in CI.
"""

import pytest

from backend.ml.upscaler import _detect_scale, scan_upscale_models


@pytest.mark.parametrize(
    "stem,expected",
    [
        # Separator after the token — matched before this change too.
        ("4x-UltraSharp", 4),
        ("4x_foolhardy_Remacri", 4),
        ("8x_NMKD-Superscale_150000_G", 8),
        ("2x Ani4Kv2", 2),
        ("ESRGAN_x4", 4),
        ("model-x2-final", 2),
        ("RealESRGAN_X4_anime", 4),  # IGNORECASE covers the capital X
        # No separator after the token — the openmodeldb convention.
        ("4xNomos8kSCHAT-L", 4),
        ("2xHFA2kAVCCompact", 2),
        ("RealESRGAN_x4plus", 4),
        # Scale 1: restoration models (denoise / deblur / JPEG / descreen).
        ("1x_ITF_SkinDiffDetail_Lite_v1", 1),
        ("1xDeJPG_SRFormer_light", 1),
        ("1x-DeH264-Lite", 1),
        ("model_x1_denoise", 1),
        # No scale in the name.
        ("SwinIR-M", None),
        ("GFPGANv1.4", None),
        # The strict leading boundary — these must stay None.
        ("Box4", None),
        ("my1xmodel", None),
        ("Model_v1x", None),
        ("HAT-L_SRx4_ImageNet-pretrain", None),
        # A longer number is not a scale.
        ("4x_NMKD-Siax_200k", 4),
        ("model_x16_something", None),
        ("12x_upscaler", None),
    ],
)
def test_detect_scale(stem: str, expected: int | None) -> None:
    assert _detect_scale(stem) == expected


def test_scan_upscale_models(tmp_path) -> None:
    """Weights are never loaded — the scan is a glob plus the heuristic."""
    (tmp_path / "4xNomos8kSCHAT-L.pth").write_bytes(b"")
    (tmp_path / "1xDeJPG_SRFormer_light.safetensors").write_bytes(b"")
    (tmp_path / "SwinIR-M.pth").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("ignored")
    (tmp_path / "subdir.pth").mkdir()

    found = {m["name"]: m for m in scan_upscale_models(tmp_path)}

    assert set(found) == {"4xNomos8kSCHAT-L", "1xDeJPG_SRFormer_light", "SwinIR-M"}
    assert found["4xNomos8kSCHAT-L"]["scale"] == 4
    assert found["1xDeJPG_SRFormer_light"]["scale"] == 1
    assert found["SwinIR-M"]["scale"] is None
    assert found["SwinIR-M"]["path"] == str(tmp_path / "SwinIR-M.pth")


def test_scan_upscale_models_missing_dir(tmp_path) -> None:
    assert scan_upscale_models(tmp_path / "nope") == []
