import os

import pytest
from PIL import Image

from common import processing


def make_image(path, fmt="PNG", size=(200, 100), mode="RGB"):
    color = (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)
    Image.new(mode, size, color).save(path, format=fmt)


def test_resize_exact_size(tmp_path):
    src = tmp_path / "in.png"
    make_image(src)
    out = processing.process_image(str(src), str(tmp_path / "out"), "resize", {"width": 50, "height": 20})
    with Image.open(out) as im:
        assert im.size == (50, 20)
        assert im.format == "PNG"


def test_thumbnail_keeps_aspect_ratio(tmp_path):
    src = tmp_path / "in.png"
    make_image(src)
    out = processing.process_image(str(src), str(tmp_path / "out"), "thumbnail", {"size": 64})
    with Image.open(out) as im:
        assert im.size == (64, 32)


def test_convert_rgba_png_to_jpeg(tmp_path):
    src = tmp_path / "in.png"
    make_image(src, mode="RGBA")
    out = processing.process_image(str(src), str(tmp_path / "out"), "convert", {"format": "jpg"})
    assert out.endswith(".jpg")
    with Image.open(out) as im:
        assert im.format == "JPEG"


def test_compress_reduces_jpeg_size(tmp_path):
    src = tmp_path / "in.jpg"
    Image.frombytes("RGB", (256, 256), os.urandom(256 * 256 * 3)).save(src, format="JPEG", quality=95)
    out = processing.process_image(str(src), str(tmp_path / "out"), "compress", {"quality": 20})
    assert os.path.getsize(out) < os.path.getsize(src)


def test_processing_is_repeatable(tmp_path):
    src = tmp_path / "in.png"
    make_image(src)
    first = processing.process_image(str(src), str(tmp_path / "out"), "resize", {"width": 10, "height": 10})
    second = processing.process_image(str(src), str(tmp_path / "out"), "resize", {"width": 10, "height": 10})
    assert first == second and os.path.exists(second)


@pytest.mark.parametrize("op,params", [
    ("rotate", {}),
    ("resize", {"width": 100}),
    ("resize", {"width": 0, "height": 10}),
    ("resize", {"width": 99999, "height": 10}),
    ("convert", {"format": "bmp"}),
    ("compress", {"quality": 500}),
])
def test_invalid_params_rejected(op, params):
    with pytest.raises(ValueError):
        processing.validate_params(op, params)


def test_inspect_upload(tmp_path):
    with pytest.raises(ValueError):
        processing.inspect_upload(b"definitely not an image")
    path = tmp_path / "x.png"
    make_image(path)
    assert processing.inspect_upload(path.read_bytes()) == "PNG"