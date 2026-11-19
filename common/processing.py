"""Image validation and processing with Pillow"""
import io
import os

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 50_000_000  # protection against decompression bombs

OPERATIONS = ("resize", "compress", "convert", "thumbnail")
ALLOWED_INPUT = {"JPEG", "PNG", "WEBP"}
FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
EXT_MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_CONVERT_TARGETS = {"jpeg": "jpeg", "jpg": "jpeg", "png": "png", "webp": "webp"}


def _int(params, key, lo, hi, default=None):
    value = params.get(key, default)
    if value is None:
        raise ValueError(f"'{key}' is required")
    if isinstance(value, bool):
        raise ValueError(f"'{key}' must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be an integer")
    if not lo <= value <= hi:
        raise ValueError(f"'{key}' must be between {lo} and {hi}")
    return value


def validate_params(operation, params):
    """Return normalised parameters or raise ValueError."""
    if operation not in OPERATIONS:
        raise ValueError(f"Unsupported operation '{operation}'. Use one of {OPERATIONS}")
    if not isinstance(params, dict):
        raise ValueError("'params' must be a JSON object")
    if operation == "resize":
        return {"width": _int(params, "width", 1, 8000), "height": _int(params, "height", 1, 8000)}
    if operation == "compress":
        return {"quality": _int(params, "quality", 1, 95, 60)}
    if operation == "thumbnail":
        return {"size": _int(params, "size", 16, 1024, 128)}
    fmt = _CONVERT_TARGETS.get(str(params.get("format", "")).lower())
    if fmt is None:
        raise ValueError("'format' must be one of jpeg, png, webp")
    return {"format": fmt}


def inspect_upload(data):
    """Check the bytes are a real, allowed, reasonably sized image. Returns its format."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            width, height = img.size
            img.verify()
    except Exception as exc:
        raise ValueError("File is not a valid image") from exc
    if fmt not in ALLOWED_INPUT:
        raise ValueError("Only JPEG, PNG and WebP images are accepted")
    if width * height > Image.MAX_IMAGE_PIXELS:
        raise ValueError("Image has too many pixels")
    return fmt


def output_format(src_format, operation, params):
    if operation == "convert":
        return params["format"].upper()
    return src_format


def _flatten_to_rgb(img):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        return background
    return img.convert("RGB")


def _normalise_mode(img):
    if img.mode in ("RGB", "RGBA", "L"):
        return img
    has_alpha = "A" in img.mode or "transparency" in img.info
    return img.convert("RGBA" if has_alpha else "RGB")


def process_image(src_path, dst_base, operation, params):
    """Process src_path and write dst_base + '.<ext>' atomically. Returns the final path.
    The output path depends only on the job, so repeating the job rewrites the same file."""
    params = validate_params(operation, params)
    with Image.open(src_path) as img:
        src_format = img.format
        img.load()
        work = ImageOps.exif_transpose(img)

    out_format = output_format(src_format, operation, params)

    if operation == "resize":
        work = work.resize((params["width"], params["height"]), Image.Resampling.LANCZOS)
    elif operation == "thumbnail":
        work.thumbnail((params["size"], params["size"]), Image.Resampling.LANCZOS)

    quality = params["quality"] if operation == "compress" else 85
    if out_format == "JPEG":
        work = _flatten_to_rgb(work)
        save_kwargs = {"quality": quality, "optimize": True}
    elif out_format == "WEBP":
        work = _normalise_mode(work)
        save_kwargs = {"quality": quality}
    else:  # PNG
        work = _normalise_mode(work)
        save_kwargs = {"optimize": True}
        if operation == "compress":
            save_kwargs["compress_level"] = 9

    final = f"{dst_base}.{FORMAT_EXT[out_format]}"
    tmp = final + ".tmp"
    os.makedirs(os.path.dirname(final), exist_ok=True)
    work.save(tmp, format=out_format, **save_kwargs)
    os.replace(tmp, final)  # atomic: readers never see a half-written file
    return final