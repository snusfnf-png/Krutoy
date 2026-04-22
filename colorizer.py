import io
import json
import gzip
import math
from PIL import Image, ImageOps, ImageEnhance


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)


def hex_to_lottie_color(hex_color: str) -> list:
    r, g, b = hex_to_rgb(hex_color)
    return [r / 255.0, g / 255.0, b / 255.0, 1.0]


def recolor_static_webp(image_data: bytes, hex_color: str) -> bytes:
    """
    Recolor preserving details:
    - Convert to grayscale to get luminance
    - Tint with target color at ~60% blend
    - Preserve original alpha
    """
    img = Image.open(io.BytesIO(image_data)).convert("RGBA")
    r_new, g_new, b_new = hex_to_rgb(hex_color)

    r, g, b, a = img.split()

    # Luminance-preserving tint
    gray = ImageOps.grayscale(img)

    # Create solid color layer
    color_layer = Image.new("L", img.size)

    # Blend: tinted = gray * color_factor
    # Use 65% tint strength — visible color but details preserved
    TINT = 0.65

    def tint_channel(lum, col):
        # Mix: lum * (1 - tint) + lum*(col/255) * tint
        # Simplified: lum * ((1-tint) + tint*col/255)
        factor = (1 - TINT) + TINT * (col / 255.0)
        return min(255, int(lum * factor))

    pixels = list(gray.getdata())
    r_pixels = [tint_channel(p, r_new) for p in pixels]
    g_pixels = [tint_channel(p, g_new) for p in pixels]
    b_pixels = [tint_channel(p, b_new) for p in pixels]

    r_ch = Image.new("L", img.size)
    g_ch = Image.new("L", img.size)
    b_ch = Image.new("L", img.size)
    r_ch.putdata(r_pixels)
    g_ch.putdata(g_pixels)
    b_ch.putdata(b_pixels)

    result = Image.merge("RGBA", (r_ch, g_ch, b_ch, a))

    out = io.BytesIO()
    result.save(out, format="WEBP", lossless=True)
    return out.getvalue()


def _recolor_lottie_color_prop(color_prop: dict, target_color: list, tint: float = 0.7):
    """
    Blend lottie color toward target while preserving luminance.
    tint=0.7 means 70% toward target color.
    """
    tr, tg, tb = target_color[0], target_color[1], target_color[2]

    def blend(orig, target):
        return orig * (1 - tint) + target * tint

    if "a" in color_prop and "k" in color_prop:
        if color_prop["a"] == 0:
            k = color_prop["k"]
            if isinstance(k, list) and len(k) >= 3:
                alpha = k[3] if len(k) > 3 else 1.0
                if alpha > 0.01:
                    # Compute luminance of original
                    lum = 0.299 * k[0] + 0.587 * k[1] + 0.114 * k[2]
                    # Tint toward target
                    color_prop["k"] = [
                        blend(k[0], tr),
                        blend(k[1], tg),
                        blend(k[2], tb),
                        alpha
                    ]
        else:
            if isinstance(color_prop["k"], list):
                for keyframe in color_prop["k"]:
                    if isinstance(keyframe, dict):
                        for key in ("s", "e"):
                            if key in keyframe and isinstance(keyframe[key], list) and len(keyframe[key]) >= 3:
                                kf = keyframe[key]
                                alpha = kf[3] if len(kf) > 3 else 1.0
                                if alpha > 0.01:
                                    keyframe[key] = [
                                        blend(kf[0], tr),
                                        blend(kf[1], tg),
                                        blend(kf[2], tb),
                                        alpha
                                    ]


def _walk_shapes(shapes: list, target_color: list):
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        ty = shape.get("ty", "")
        if ty in ("fl", "st"):
            if "c" in shape:
                _recolor_lottie_color_prop(shape["c"], target_color)
        if "it" in shape:
            _walk_shapes(shape["it"], target_color)


def _walk_layers(layers: list, target_color: list):
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if "layers" in layer:
            _walk_layers(layer["layers"], target_color)
        if "shapes" in layer:
            _walk_shapes(layer["shapes"], target_color)
        # Effects
        if "ef" in layer:
            for ef in layer["ef"]:
                if isinstance(ef, dict) and "ef" in ef:
                    for sub in ef["ef"]:
                        if isinstance(sub, dict) and sub.get("ty") == 2 and "v" in sub:
                            _recolor_lottie_color_prop(sub["v"], target_color)


def recolor_tgs(tgs_data: bytes, hex_color: str) -> bytes:
    target_color = hex_to_lottie_color(hex_color)

    json_data = gzip.decompress(tgs_data)
    lottie_json = json.loads(json_data)

    if "layers" in lottie_json:
        _walk_layers(lottie_json["layers"], target_color)

    if "assets" in lottie_json:
        for asset in lottie_json["assets"]:
            if isinstance(asset, dict) and "layers" in asset:
                _walk_layers(asset["layers"], target_color)

    out_json = json.dumps(lottie_json, separators=(",", ":")).encode("utf-8")
    return gzip.compress(out_json, compresslevel=9)


def recolor_webm(webm_data: bytes, hex_color: str) -> bytes:
    """Recolor WebM video sticker using ffmpeg hue/colorize filter."""
    import subprocess
    import tempfile
    import os

    r, g, b = hex_to_rgb(hex_color)

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
        fin.write(webm_data)
        fin_path = fin.name

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fout:
        fout_path = fout.name

    try:
        # Desaturate partially then colorize — preserves details
        # hue=s=0 full desat, then colorize with low saturation
        rf, gf, bf = r / 255.0, g / 255.0, b / 255.0

        # Mix matrix: 65% toward color, 35% original luminance
        TINT = 0.65
        # Luminance weights
        lw = [0.299, 0.587, 0.114]

        rr = lw[0] * (1 - TINT) + rf * TINT
        rg = lw[1] * (1 - TINT)
        rb = lw[2] * (1 - TINT)
        gr = lw[0] * (1 - TINT)
        gg = lw[1] * (1 - TINT) + gf * TINT
        gb = lw[2] * (1 - TINT)
        br = lw[0] * (1 - TINT)
        bg = lw[1] * (1 - TINT)
        bb = lw[2] * (1 - TINT) + bf * TINT

        vf = (
            f"colorchannelmixer="
            f"rr={rr:.4f}:rg={rg:.4f}:rb={rb:.4f}:"
            f"gr={gr:.4f}:gg={gg:.4f}:gb={gb:.4f}:"
            f"br={br:.4f}:bg={bg:.4f}:bb={bb:.4f}"
        )

        cmd = [
            "ffmpeg", "-y", "-i", fin_path,
            "-vf", vf,
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",
            fout_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)

        if result.returncode != 0:
            return webm_data  # Return original if ffmpeg fails

        with open(fout_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(fin_path)
        try:
            os.unlink(fout_path)
        except Exception:
            pass
    
