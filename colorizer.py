import io
import json
import gzip
from PIL import Image, ImageOps
import colorsys


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)


def rgb_to_hls(r, g, b):
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def hls_to_rgb(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


# ─────────────────────────────────────────────
# STATIC WEBP / PNG
# ─────────────────────────────────────────────

def recolor_static_webp(image_data: bytes, hex_color: str) -> bytes:
    """
    Hue-shift recolor:
    1. Convert image to HLS
    2. Replace hue with target hue
    3. Boost saturation
    4. Keep original lightness (preserves shadows/highlights/details)
    """
    img = Image.open(io.BytesIO(image_data)).convert("RGBA")
    r_new, g_new, b_new = hex_to_rgb(hex_color)
    h_target, l_target, s_target = rgb_to_hls(r_new, g_new, b_new)

    pixels = list(img.getdata())
    new_pixels = []

    for pixel in pixels:
        r, g, b, a = pixel
        if a < 10:
            new_pixels.append(pixel)
            continue

        h_orig, l_orig, s_orig = rgb_to_hls(r, g, b)

        # Replace hue, keep original lightness, boost saturation
        new_s = min(1.0, max(s_orig, 0.3) * 1.2)
        # If target is very desaturated (white/black/gray), reduce saturation
        if s_target < 0.1:
            new_s = s_target
            new_h = h_target
        else:
            new_h = h_target
            new_s = min(1.0, s_orig * 0.4 + s_target * 0.6)

        nr, ng, nb = hls_to_rgb(new_h, l_orig, new_s)
        new_pixels.append((nr, ng, nb, a))

    result = Image.new("RGBA", img.size)
    result.putdata(new_pixels)

    out = io.BytesIO()
    result.save(out, format="WEBP", lossless=True)
    return out.getvalue()


# ─────────────────────────────────────────────
# LOTTIE / TGS
# ─────────────────────────────────────────────

def _lottie_color_to_hls(color_list):
    """Convert lottie [r,g,b,a] (0..1) to hls."""
    r, g, b = color_list[0], color_list[1], color_list[2]
    return colorsys.rgb_to_hls(r, g, b)


def _apply_hue_to_lottie_color(color_list, h_target, s_target):
    """Replace hue in a lottie color value, keep lightness."""
    if len(color_list) < 3:
        return color_list
    r, g, b = color_list[0], color_list[1], color_list[2]
    alpha = color_list[3] if len(color_list) > 3 else 1.0

    if alpha < 0.01:
        return color_list

    h_orig, l_orig, s_orig = colorsys.rgb_to_hls(r, g, b)

    if s_target < 0.05:
        # Target is gray/white/black — desaturate
        new_s = 0.0
        new_h = h_target
    else:
        new_h = h_target
        new_s = min(1.0, s_orig * 0.4 + s_target * 0.6)

    nr, ng, nb = colorsys.hls_to_rgb(new_h, l_orig, new_s)
    return [nr, ng, nb, alpha]


def _recolor_color_prop(prop: dict, h_target: float, s_target: float):
    """Recolor a lottie color property (animated or static)."""
    if not isinstance(prop, dict) or "k" not in prop:
        return

    if prop.get("a", 0) == 0:
        # Static
        k = prop["k"]
        if isinstance(k, list) and len(k) >= 3 and isinstance(k[0], (int, float)):
            prop["k"] = _apply_hue_to_lottie_color(k, h_target, s_target)
    else:
        # Animated keyframes
        if isinstance(prop["k"], list):
            for kf in prop["k"]:
                if not isinstance(kf, dict):
                    continue
                for key in ("s", "e"):
                    if key in kf and isinstance(kf[key], list) and len(kf[key]) >= 3:
                        if isinstance(kf[key][0], (int, float)):
                            kf[key] = _apply_hue_to_lottie_color(kf[key], h_target, s_target)


def _recolor_gradient_prop(prop: dict, h_target: float, s_target: float):
    """Recolor gradient color stops in lottie."""
    if not isinstance(prop, dict) or "k" not in prop:
        return

    def process_stops(stops_flat, n_colors):
        """stops_flat is flat array: [offset, r, g, b, offset, r, g, b, ...]"""
        if not isinstance(stops_flat, list):
            return stops_flat
        result = list(stops_flat)
        i = 0
        count = 0
        while i + 3 < len(result) and count < n_colors:
            offset = result[i]
            r, g, b = result[i+1], result[i+2], result[i+3]
            h_o, l_o, s_o = colorsys.rgb_to_hls(r, g, b)
            if s_target < 0.05:
                new_s, new_h = 0.0, h_target
            else:
                new_h = h_target
                new_s = min(1.0, s_o * 0.4 + s_target * 0.6)
            nr, ng, nb = colorsys.hls_to_rgb(new_h, l_o, new_s)
            result[i+1], result[i+2], result[i+3] = nr, ng, nb
            i += 4
            count += 1
        return result

    # Number of color stops
    n = prop.get("p", 4)  # default 4 stops

    if prop.get("a", 0) == 0:
        k = prop["k"]
        if isinstance(k, list):
            prop["k"] = process_stops(k, n)
    else:
        if isinstance(prop.get("k"), list):
            for kf in prop["k"]:
                if isinstance(kf, dict):
                    for key in ("s", "e"):
                        if key in kf and isinstance(kf[key], list):
                            kf[key] = process_stops(kf[key], n)


def _walk_shapes(shapes: list, h_target: float, s_target: float):
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        ty = shape.get("ty", "")

        if ty == "fl":  # Fill
            if "c" in shape:
                _recolor_color_prop(shape["c"], h_target, s_target)

        elif ty == "st":  # Stroke
            if "c" in shape:
                _recolor_color_prop(shape["c"], h_target, s_target)

        elif ty in ("gf", "gs"):  # Gradient fill / stroke
            if "g" in shape:
                g_data = shape["g"]
                if isinstance(g_data, dict) and "k" in g_data:
                    _recolor_gradient_prop(g_data, h_target, s_target)

        if "it" in shape:
            _walk_shapes(shape["it"], h_target, s_target)


def _walk_layers(layers: list, h_target: float, s_target: float):
    for layer in layers:
        if not isinstance(layer, dict):
            continue

        # Precompose / nested layers
        if "layers" in layer:
            _walk_layers(layer["layers"], h_target, s_target)

        # Shapes
        if "shapes" in layer:
            _walk_shapes(layer["shapes"], h_target, s_target)

        # Solid layer color (ty=1)
        if layer.get("ty") == 1 and "sc" in layer:
            sc = layer["sc"]
            if isinstance(sc, str) and sc.startswith("#"):
                r2, g2, b2 = hex_to_rgb(sc.lstrip("#"))
                h_o, l_o, s_o = colorsys.rgb_to_hls(r2/255, g2/255, b2/255)
                new_s = 0.0 if s_target < 0.05 else min(1.0, s_o * 0.4 + s_target * 0.6)
                nr, ng, nb = colorsys.hls_to_rgb(h_target, l_o, new_s)
                layer["sc"] = "#{:02x}{:02x}{:02x}".format(int(nr*255), int(ng*255), int(nb*255))

        # Effects (e.g. tint effect ty=20, fill effect ty=21)
        if "ef" in layer:
            for ef in layer["ef"]:
                if not isinstance(ef, dict):
                    continue
                ef_ty = ef.get("ty")
                if "ef" in ef:
                    for sub in ef["ef"]:
                        if isinstance(sub, dict) and sub.get("ty") == 2 and "v" in sub:
                            _recolor_color_prop(sub["v"], h_target, s_target)


def recolor_tgs(tgs_data: bytes, hex_color: str) -> bytes:
    r_new, g_new, b_new = hex_to_rgb(hex_color)
    h_target, l_target, s_target = colorsys.rgb_to_hls(r_new/255, g_new/255, b_new/255)

    json_data = gzip.decompress(tgs_data)
    lottie_json = json.loads(json_data)

    if "layers" in lottie_json:
        _walk_layers(lottie_json["layers"], h_target, s_target)

    if "assets" in lottie_json:
        for asset in lottie_json["assets"]:
            if isinstance(asset, dict) and "layers" in asset:
                _walk_layers(asset["layers"], h_target, s_target)

    out_json = json.dumps(lottie_json, separators=(",", ":")).encode("utf-8")
    return gzip.compress(out_json, compresslevel=9)


# ─────────────────────────────────────────────
# VIDEO WEBM
# ─────────────────────────────────────────────

def recolor_webm(webm_data: bytes, hex_color: str) -> bytes:
    import subprocess
    import tempfile
    import os

    r, g, b = hex_to_rgb(hex_color)
    h_target, l_target, s_target = colorsys.rgb_to_hls(r/255, g/255, b/255)

    # Convert target hue to degrees for ffmpeg (hue filter uses radians or degrees)
    hue_deg = h_target * 360

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fin:
        fin.write(webm_data)
        fin_path = fin.name
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as fout:
        fout_path = fout.name

    try:
        # hue= shifts hue, s= sets saturation multiplier
        # We shift to target hue, keep original lightness
        sat_mult = 1.5 if s_target > 0.1 else 0.0
        vf = f"hue=H={hue_deg}:s={sat_mult:.2f}"

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
            return webm_data

        with open(fout_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(fin_path)
        try:
            os.unlink(fout_path)
        except Exception:
            pass
                         
