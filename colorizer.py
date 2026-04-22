import io
import json
import gzip
import struct
import re
import math
from PIL import Image, ImageOps
import cairosvg
import lottie
from lottie import objects as lo
from lottie.exporters import exporters as lottie_exporters
from lottie.parsers import parsers as lottie_parsers


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return r, g, b


def hex_to_lottie_color(hex_color: str) -> list[float]:
    r, g, b = hex_to_rgb(hex_color)
    return [r / 255.0, g / 255.0, b / 255.0, 1.0]


def recolor_static_webp(image_data: bytes, hex_color: str) -> bytes:
    """Recolor a static WebP/PNG sticker."""
    img = Image.open(io.BytesIO(image_data)).convert("RGBA")
    r_new, g_new, b_new = hex_to_rgb(hex_color)

    r, g, b, a = img.split()

    # Convert to grayscale luminance for preserving shadows/highlights
    gray = ImageOps.grayscale(img)

    # Create new colored image
    r_channel = gray.point(lambda x: int(x * r_new / 255))
    g_channel = gray.point(lambda x: int(x * g_new / 255))
    b_channel = gray.point(lambda x: int(x * b_new / 255))

    colored = Image.merge("RGBA", (r_channel, g_channel, b_channel, a))

    out = io.BytesIO()
    colored.save(out, format="WEBP", lossless=True)
    return out.getvalue()


def recolor_static_png(image_data: bytes, hex_color: str) -> bytes:
    """Recolor a static PNG sticker."""
    img = Image.open(io.BytesIO(image_data)).convert("RGBA")
    r_new, g_new, b_new = hex_to_rgb(hex_color)

    r, g, b, a = img.split()
    gray = ImageOps.grayscale(img)

    r_channel = gray.point(lambda x: int(x * r_new / 255))
    g_channel = gray.point(lambda x: int(x * g_new / 255))
    b_channel = gray.point(lambda x: int(x * b_new / 255))

    colored = Image.merge("RGBA", (r_channel, g_channel, b_channel, a))

    out = io.BytesIO()
    colored.save(out, format="PNG")
    return out.getvalue()


def _recolor_lottie_value(val, target_color: list):
    """Recursively replace color values in lottie JSON."""
    if isinstance(val, dict):
        # Color keyframe animated
        if val.get("ty") == "st" or val.get("ty") == "fl":
            # stroke or fill
            if "c" in val:
                _recolor_lottie_color_prop(val["c"], target_color)
        else:
            for k, v in val.items():
                if k == "c" and isinstance(v, dict):
                    _recolor_lottie_color_prop(v, target_color)
                else:
                    _recolor_lottie_value(v, target_color)
    elif isinstance(val, list):
        for item in val:
            _recolor_lottie_value(item, target_color)


def _recolor_lottie_color_prop(color_prop: dict, target_color: list):
    """Replace color values in a lottie color property (animated or static)."""
    if "a" in color_prop and "k" in color_prop:
        if color_prop["a"] == 0:
            # Static color
            if isinstance(color_prop["k"], list) and len(color_prop["k"]) >= 3:
                # Preserve alpha
                alpha = color_prop["k"][3] if len(color_prop["k"]) > 3 else 1.0
                if alpha > 0:
                    color_prop["k"] = [target_color[0], target_color[1], target_color[2], alpha]
        else:
            # Animated color keyframes
            if isinstance(color_prop["k"], list):
                for keyframe in color_prop["k"]:
                    if isinstance(keyframe, dict):
                        for key in ("s", "e"):
                            if key in keyframe and isinstance(keyframe[key], list) and len(keyframe[key]) >= 3:
                                alpha = keyframe[key][3] if len(keyframe[key]) > 3 else 1.0
                                if alpha > 0:
                                    keyframe[key] = [target_color[0], target_color[1], target_color[2], alpha]


def _walk_lottie_layers(layers: list, target_color: list):
    """Walk through all lottie layers and recolor."""
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        # Recurse into precompose layers
        if "layers" in layer:
            _walk_lottie_layers(layer["layers"], target_color)
        # Walk shapes
        if "shapes" in layer:
            _walk_shapes(layer["shapes"], target_color)
        # Walk effects
        if "ef" in layer:
            _walk_lottie_value_list(layer["ef"], target_color)


def _walk_shapes(shapes: list, target_color: list):
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        ty = shape.get("ty", "")
        if ty in ("fl", "st", "gf", "gs"):
            if "c" in shape:
                _recolor_lottie_color_prop(shape["c"], target_color)
        if "it" in shape:
            _walk_shapes(shape["it"], target_color)


def _walk_lottie_value_list(items: list, target_color: list):
    for item in items:
        if isinstance(item, dict):
            _recolor_lottie_value(item, target_color)


def recolor_tgs(tgs_data: bytes, hex_color: str) -> bytes:
    """Recolor an animated TGS sticker (gzip'd lottie JSON)."""
    target_color = hex_to_lottie_color(hex_color)

    # Decompress TGS (gzipped JSON)
    json_data = gzip.decompress(tgs_data)
    lottie_json = json.loads(json_data)

    # Walk all layers and recolor
    if "layers" in lottie_json:
        _walk_lottie_layers(lottie_json["layers"], target_color)

    # Also walk assets (precomps)
    if "assets" in lottie_json:
        for asset in lottie_json["assets"]:
            if isinstance(asset, dict) and "layers" in asset:
                _walk_lottie_layers(asset["layers"], target_color)

    # Recompress
    out_json = json.dumps(lottie_json, separators=(",", ":")).encode("utf-8")
    out_tgs = gzip.compress(out_json, compresslevel=9)
    return out_tgs


def recolor_webm(webm_data: bytes, hex_color: str) -> bytes:
    """
    For WebM video stickers, we use ffmpeg via subprocess to apply colorize filter.
    Returns recolored WebM bytes.
    """
    import subprocess
    import tempfile

    r, g, b = hex_to_rgb(hex_color)
    # Convert to hue/saturation for ffmpeg colorize
    # Use hue+colorize filter
    h, s, v = _rgb_to_hsv(r, g, b)
    hue_deg = h * 360

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
        fin.write(webm_data)
        fin_path = fin.name

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fout:
        fout_path = fout.name

    try:
        # Use ffmpeg to apply hue/colorize
        # We desaturate then apply new hue
        cmd = [
            "ffmpeg", "-y", "-i", fin_path,
            "-vf", f"hue=H={hue_deg}:s=3,colorchannelmixer=rr={r/255:.3f}:gg={g/255:.3f}:bb={b/255:.3f}",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",
            fout_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            # Fallback: simpler colorize
            cmd2 = [
                "ffmpeg", "-y", "-i", fin_path,
                "-vf", f"colorchannelmixer=rr={r/255:.3f}:rg=0:rb=0:gr=0:gg={g/255:.3f}:gb=0:br=0:bg=0:bb={b/255:.3f}",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-auto-alt-ref", "0",
                fout_path
            ]
            subprocess.run(cmd2, capture_output=True, timeout=60)

        with open(fout_path, "rb") as f:
            return f.read()
    finally:
        import os
        os.unlink(fin_path)
        try:
            os.unlink(fout_path)
        except Exception:
            pass


def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    diff = mx - mn
    v = mx
    s = 0.0 if mx == 0 else diff / mx
    h = 0.0
    if diff != 0:
        if mx == r:
            h = (g - b) / diff % 6
        elif mx == g:
            h = (b - r) / diff + 2
        else:
            h = (r - g) / diff + 4
        h /= 6
    return h, s, v
    
