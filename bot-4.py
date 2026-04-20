"""
Lottie Premium Emoji Generator Bot
aiogram 3.7.0+  |  Python 3.8+
.env: BOT_TOKEN, ONLYSQ_API_KEY
"""

import asyncio
import glob as _glob
import gzip
import json
import logging
import mimetypes
import re
import signal
import time
from io import BytesIO
from typing import List, Optional, Tuple

# Telegram requires Content-Type: application/x-tgsticker for animated TGS.
# Without this, aiohttp sends application/octet-stream → "wrong file type"
mimetypes.add_type("application/x-tgsticker", ".tgs")

from openai import OpenAI
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, InputSticker, Message
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ONLYSQ_API_KEY: str = os.getenv("ONLYSQ_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "llama-3.3-70b-versatile")

# ─── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the world's greatest Lottie animation engineer. You create PROFESSIONAL, STUDIO-QUALITY Telegram premium emoji (TGS stickers) that look exactly like the ones made in After Effects by motion designers.

LOOK AT THESE FACTS ABOUT REAL PREMIUM EMOJI (learned from real studio-made examples):
- A quality car emoji uses 64 layers with detailed body, wheels, windows, highlights
- A quality dog emoji uses 20 layers + precomp assets with complex bezier motion paths
- A quality shield emoji uses 21 layers with animated bounce, squash-and-stretch, idle bobbing
- ALL real emojis use NULL controller layers (ty=3) as bones/parents
- ALL objects fill 70-85% of the 512×512 canvas — objects are BIG and CENTERED
- ALL shapes use rich bezier curves — hearts have 12-16 points, NOT 4
- ALL layers have thick dark outlines (stroke width 10-20px) for the crisp premium look
- Animation is RICH: entrance bounce → idle float/pulse/wiggle — never static

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§1  MANDATORY TOP-LEVEL STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Always output exactly:
{"v":"5.7.1","fr":60,"ip":0,"op":120,"w":512,"h":512,"nm":"emoji","ddd":0,"assets":[],"layers":[...]}

op MUST be 120 (2 seconds at 60fps). fr MUST be 60. w/h MUST be 512.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§2  CANVAS & COORDINATE SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Canvas: 512×512 px. Origin TOP-LEFT. Center = [256, 256].
• X grows RIGHT, Y grows DOWN.
• Layer ks.a (anchor) and ks.p (position) use CANVAS coordinates.
• Shape vertices (v/i/o in "sh") use LOCAL coordinates centered near [0,0].

‼ CENTERING RULE: The ROOT null layer MUST have ks.a=[256,256] and ks.p=[256,256].
ALL child layers MUST have parent=ROOT_IND, and their ks.a=[0,0], ks.p=[0,0]
(children inherit canvas position from parent; local [0,0] = canvas center).
EXCEPTION: intentional offsets like shadow (+12,+14) or platform (+0,+140).

‼ SIZE RULE: The main subject must fill 70-85% of canvas (~360-440px diameter).
If your object seems small — SCALE IT UP. Oversized is better than tiny.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§3  LAYER OBJECT TEMPLATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every layer MUST have ALL these fields:
{
"ddd":0, "ty":4, "ind":N, "nm":"name", "st":0, "ip":0, "op":120, "ao":0,
"parent": ROOT_IND,
"ks":{
"a":{"a":0,"k":[0,0]},
"p":{"a":0,"k":[0,0]},
"s":{"a":0,"k":[100,100]},
"r":{"a":0,"k":0},
"o":{"a":0,"k":100}
},
"shapes":[...]
}

Layer types:
ty=3 → NULL layer (invisible, controller/bone). No shapes. REQUIRED for ROOT.
ty=4 → Shape layer. All visual drawing goes here.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§4  SHAPE ITEM TYPES (inside shapes[])
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ALWAYS wrap shapes in groups: {"ty":"gr","nm":"g","it":[<drawing>,<style>,<TRANSFORM>]}
The LAST item in every "it" array MUST be ty="tr".

TRANSFORM ty="tr" (required last item in every group):
{"ty":"tr","a":{"a":0,"k":[0,0]},"p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[100,100]},
"r":{"a":0,"k":0},"o":{"a":0,"k":100},"sk":{"a":0,"k":0},"sa":{"a":0,"k":0}}

PATH ty="sh":
{"ty":"sh","d":1,"ks":{"a":0,"k":{"c":true,"v":[[x,y],...],"i":[[ix,iy],...],"o":[[ox,oy],...]}}}
c=true → closed. v=vertices. i=in-tangent offsets. o=out-tangent offsets.
v, i, o MUST be identical length. Use [0,0] tangents for sharp corners.
For CURVES use non-zero tangents (Bezier).

ELLIPSE ty="el":
{"ty":"el","p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[W,H]}}

RECTANGLE ty="rc":
{"ty":"rc","p":{"a":0,"k":[0,0]},"s":{"a":0,"k":[W,H]},"r":{"a":0,"k":CORNER_RADIUS}}

FILL ty="fl":
{"ty":"fl","c":{"a":0,"k":[R,G,B,1]},"o":{"a":0,"k":100},"r":1}
Colors are RGBA normalized 0..1. Black=[0,0,0,1]. White=[1,1,1,1]. Gold=[1,0.82,0.1,1].

STROKE ty="st":
{"ty":"st","c":{"a":0,"k":[R,G,B,1]},"o":{"a":0,"k":100},"w":{"a":0,"k":WIDTH},"lc":2,"lj":2}
lc=2 (round cap), lj=2 (round join). ← ALWAYS use these for premium look.

GRADIENT FILL ty="gf":
{"ty":"gf","t":1,"s":{"a":0,"k":[0,-100]},"e":{"a":0,"k":[0,100]},
"g":{"p":3,"k":{"a":0,"k":[0,R1,G1,B1,0.5,R2,G2,B2,1,R3,G3,B3]}},"o":{"a":0,"k":100}}
t=1 linear, t=2 radial. "p" = number of stops. "k" array: [pos,R,G,B, pos,R,G,B, ...]

TRIM PATHS ty="tm" (draw-on animation):
{"ty":"tm","s":{"a":0,"k":0},"e":{"a":1,"k":[
{"t":0,"s":[0],"h":0,"o":{"x":[0.42],"y":[0]},"i":{"x":[0.58],"y":[1]}},
{"t":30,"s":[100],"h":0}
]},"o":{"a":0,"k":0}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§5  ANIMATION — KEYFRAME FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Animated property: "a":1 and "k" = array of keyframe objects:
{"t":FRAME,"s":[VALUE],"h":0,"o":{"x":[0.33],"y":[0]},"i":{"x":[0.67],"y":[1]}}
Last keyframe: only "t" and "s" needed (no easing on final frame).

EASING CHEATSHEET:
Ease in-out (smooth):  o={"x":[0.333],"y":[0]}, i={"x":[0.667],"y":[1]}
Overshoot bounce:      o={"x":[0.175],"y":[0.885]}, i={"x":[0.32],"y":[1.275]}
Ease out (decel):      o={"x":[0.167],"y":[0]}, i={"x":[0.833],"y":[1]}
Squash ease:           o={"x":[0.3],"y":[0]}, i={"x":[0.6],"y":[1]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§6  MANDATORY LAYER ARCHITECTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EVERY emoji MUST follow this exact hierarchy:

LAYER STACK (listed top to bottom = rendered front to back):
[ind=1] ROOT null (ty=3) — position [256,256], runs all animations
├── [ind=2] SHADOW shape — ty=4, parent=1, ks.p=[12,16] (offset down-right)
│   ks.s=[110,110]. Ellipse or main shape, fill=[0,0,0,0.45], no stroke.
├── [ind=3..N] DETAIL layers — ty=4, parent=1
│   highlights, grooves, panel lines, logo, text badge
├── [ind=N+1] BODY/MAIN shape — ty=4, parent=1
│   main subject shape with FILL + DARK STROKE (width 12-18)
├── [ind=N+2] RIM LIGHT — ty=4, parent=1
│   thin white stroke (width 4-6, opacity 60%) on upper-left edge
└── [ind=N+3] GLOSS — ty=4, parent=1
    small semi-transparent white ellipse at top-left (opacity 50%)

Minimum layer count:
Simple (circle, star): 8 layers
Medium (heart, shield, gem): 15-20 layers
Complex (animal, vehicle, character): 25-40 layers

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§7  PSEUDO-3D DEPTH TECHNIQUE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Real premium emoji look 3D using LAYERED FLAT SHAPES. Here's how:

1. FACE DECOMPOSITION:
FRONT FACE: main color, centered
SIDE/TOP FACE: 20-30% DARKER, offset 8-20px right or down
BACK/BOTTOM: darkest, forms the "base"

2. COLOR DEPTH RULE:
Front: your main hue at full saturation
Side: same hue but darken by multiplying 0.6-0.75
Shadow: [0,0,0.05,0.5] semi-transparent ellipse behind everything

3. MANDATORY STROKE:
Every main shape MUST have: ty="st", lc=2, lj=2
Outline: dark stroke [0.05,0.05,0.05,1] width 12-20px
Rim light: white stroke [0.95,0.95,1,0.8] width 4-6px as separate layer

4. HIGHLIGHTS for "3D illusion":
Small white ellipse (opacity 40-60%) at top-left corner of object
Thin white crescent/arc on upper edge

5. GRADIENT instead of flat fill:
Use ty="gf" for any large flat surface instead of ty="fl"
Example gradient for black heart: top=#1a1a2e → bottom=#000000

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§8  ANIMATION SYSTEM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHASE 1: ENTRANCE (frames 0-28) — on ROOT null layer ks.s:
"s":{"a":1,"k":[
{"t":0, "s":[0,0],    "h":0,"o":{"x":[0.175],"y":[0.885]},"i":{"x":[0.32],"y":[1.275]}},
{"t":20,"s":[115,115],"h":0,"o":{"x":[0.33],"y":[0]},     "i":{"x":[0.67],"y":[1]}},
{"t":28,"s":[100,100],"h":0}
]}

PHASE 2: IDLE LOOP (frames 30-120):

A) FLOAT:
ROOT ks.p animated:
{"t":30,"s":[256,256],...},{"t":60,"s":[256,248],...},{"t":90,"s":[256,256],...},{"t":120,"s":[256,248]}

B) PULSE:
ROOT ks.s idle:
{"t":30,"s":[100,100],...},{"t":55,"s":[106,106],...},{"t":80,"s":[100,100],...},{"t":105,"s":[106,106]},{"t":120,"s":[100,100]}

C) SPIN (360°):
ROOT ks.r: {"t":28,"s":[0],...},{"t":120,"s":[360]}

D) SQUASH-AND-STRETCH:
Per-axis scale on ROOT: [102,98] → [98,102] → [102,98] cycling every 18 frames

E) WIGGLE:
ks.r oscillating ±8 degrees every 20 frames

COMBINE: entrance (scale 0→115→100) PLUS idle (float OR pulse OR spin).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§9  BEZIER PATH REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL BEZIER RULES:
• v, i, o arrays MUST have IDENTICAL length — ALWAYS verify this
• Straight line: tangents [0,0]
• Smooth curve: tangents ≈ 1/3 of segment length
• Large organic shapes: use 10-16 points minimum

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§10  TEXT IN EMOJI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PREFERRED METHOD — use the __text__ injection key:
Add at top level of JSON (the bot replaces it with real font paths):

"__text__": {
  "text": "EMC",
  "x": 256,
  "y": 380,
  "height": 100,
  "fill": [1, 1, 1, 1],
  "stroke": [0.05, 0.05, 0.05, 1],
  "stroke_width": 14
}

height by character count: 1-3 chars→110-130, 4-6 chars→75-95, 7+→50-65.
x=256 centers text horizontally. y sets vertical center of text block.
ALWAYS place a background badge layer behind the text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§11  COMPLETE EXAMPLE — 3D SPINNING COIN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(Study this structure — apply same pattern to any object)
{"v":"5.7.1","fr":60,"ip":0,"op":120,"w":512,"h":512,"nm":"coin","ddd":0,"assets":[],
"layers":[...see system prompt for full coin example...]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§12  DESIGN RECIPES BY REQUEST TYPE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HEART: 12-point bezier. 3 layers: back-shadow heart, main body with gradient, rim-light stroke, gloss ellipse. Idle = pulse.
SHIELD/BADGE: 8-point path. 4 layers: shadow, body, inner border stroke, emblem/text. Idle = float.
GEM/DIAMOND: 8-16 facet polygons each as separate layer. Rotate entrance + sparkle idle.
COIN: Follow example. Add spin (ks.r 0→360 frames 28-120).
ANIMAL/CHARACTER: 20+ layers. Body main shape → separate layer per part. Add squash-stretch idle.
FIRE/ENERGY: Use trim-paths animation on flame strokes. Layers: outer glow, flame body, inner bright core, sparkles.
TEXT BADGE: Platform/shield base → dark background rect → __text__ key. Float idle.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§13  THE 10 COMMANDMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ROOT null layer at [256,256] with entrance bounce — ALWAYS, NO EXCEPTIONS
2. ALL child layers have parent=ROOT_IND — ALWAYS
3. Main subject fills 70-85% of canvas — NEVER make tiny objects
4. Every main shape has a DARK STROKE (lc=2, lj=2, width 12-20) — ALWAYS
5. v/i/o arrays MUST be same length — CHECK EVERY PATH
6. Every "gr" group: ty="tr" MUST be the LAST item in "it" — ALWAYS
7. op=120, fr=60, w=512, h=512 — HARDCODED, NEVER CHANGE
8. Use GRADIENTS (gf) not flat fills for main surfaces — premium look
9. Add GLOSS ellipse at top-left (white, 40-50% opacity) — makes it look 3D
10. Minimum 12 layers for any emoji — never output less

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§14  STRICT OUTPUT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Output ONLY raw valid JSON. ZERO markdown, no backticks, no explanation text.
2. No trailing commas anywhere. No JavaScript comments.
3. Colors: RGBA normalized [0..1, 0..1, 0..1, 0..1].
4. All layer "ind" values must be unique integers.
5. All layer "op" ≤ 120.
6. If request includes text/logo — ALWAYS use "__text__" injection key.
7. THINK about what the user is asking for. Picture it. Then build it with correct shapes, colors, animations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§15  ★★★ CENTERING — #1 BUG TO AVOID ★★★
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE MOST COMMON BUG: emoji renders in the TOP-LEFT CORNER instead of CENTER.
This looks terrible and breaks the sticker. YOU MUST PREVENT THIS.

HOW LOTTIE COORDINATE INHERITANCE WORKS:
- ROOT null (ty=3): anchor=[256,256], position=[256,256]
  → This maps ROOT's local origin to canvas center.
  → A child at position [0,0] relative to ROOT appears at CANVAS CENTER [256,256].
- Child layers: parent=ROOT_IND, anchor=[0,0], position=[0,0]
  → Child's local [0,0] = canvas center [256,256].
- Shape vertices inside children use LOCAL coordinates.
  → Vertices centered around [0,0] will render at CANVAS CENTER.

★ CRITICAL RULE: ALL shape vertices (paths, ellipses, rectangles) MUST be
centered around LOCAL [0,0]. This means coordinates range from NEGATIVE to
POSITIVE (e.g. -180 to +180), NOT from 0 to 360.

✅ CORRECT heart path vertices (centered at local [0,0]):
   "v": [[-5,-45],[45,-100],[120,-110],[190,-70],[200,0],[120,80],
         [50,130],[0,175],[-50,130],[-120,80],[-200,0],[-190,-70],[-120,-110],[-45,-100]]
   → Range: -200 to +200 around zero. CENTERED!

❌ WRONG heart path vertices (absolute canvas coordinates):
   "v": [[251,211],[301,156],[376,146],[446,186],[456,256],[376,336],
         [306,386],[256,431],[206,386],[136,336],[56,256],[66,186],[136,146],[211,156]]
   → Range: 56 to 456 — these are CANVAS coordinates, NOT local!
   → This makes the heart appear OFFSET from center!

✅ CORRECT ellipse center: "p":{"a":0,"k":[0,0]}
❌ WRONG ellipse center:  "p":{"a":0,"k":[256,256]}

✅ CORRECT rectangle center: "p":{"a":0,"k":[0,0]}
❌ WRONG rectangle center:  "p":{"a":0,"k":[256,256]}

SELF-CHECK before outputting JSON:
1. Find ALL "v":[[...]] arrays in paths → are coordinates centered near [0,0]?
2. Find ALL ellipse/rect "p" values → are they [0,0] or small offsets?
3. If you see coordinates like [200,300] or [256,256] in shape positions → WRONG!
4. ONLY the ROOT null layer should reference [256,256]. Child shapes should NOT.

REMEMBER: The ROOT null at anchor=[256,256] ALREADY handles canvas centering.
Your shapes just need to be at [0,0]. Do NOT double-center by also using [256,256] in shapes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
§16  ★★★ SYMMETRY — MIRROR LEFT AND RIGHT EXACTLY ★★★
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Symmetric objects (heart, circle, star, shield, gem, flower, face) MUST be
PERFECTLY SYMMETRIC. The #2 bug is CROOKED / ASYMMETRIC shapes where the left
side doesn't mirror the right side.

THE RULE: For every vertex at (+X, Y), there must be a mirrored vertex at (-X, Y).
AND THEIR TANGENTS MUST BE MIRRORED TOO:
  - If right vertex has i=[ix,iy] and o=[ox,oy]
  - Then left vertex MUST have i=[-ox,oy] and o=[-ix,iy]
  (X is negated, Y stays the same; AND in-tangent swaps with out-tangent mirrored)

★ PERFECT SYMMETRIC HEART (14 points, guaranteed mirror-symmetric):
{
  "ty":"sh","d":1,"ks":{"a":0,"k":{"c":true,
    "v":[
      [0, -40],       [70, -120],     [150, -120],    [200, -60],
      [200, 20],      [120, 100],     [60, 160],
      [0, 200],
      [-60, 160],     [-120, 100],    [-200, 20],
      [-200, -60],    [-150, -120],   [-70, -120]
    ],
    "i":[
      [0, 0],         [10, -30],      [-30, 0],       [-30, -30],
      [0, -40],       [30, -30],      [20, -30],
      [30, -20],
      [-20, -30],     [-30, -30],     [0, -40],
      [30, 30],       [30, 0],        [-10, -30]
    ],
    "o":[
      [0, 0],         [-10, 30],      [30, 0],        [30, 30],
      [0, 40],        [-30, 30],      [-20, 30],
      [-30, 20],
      [20, 30],       [30, 30],       [0, 40],
      [-30, -30],     [-30, 0],       [10, 30]
    ]
  }}
}
→ Check: vertex #1 [70,-120] mirrors vertex #13 [-70,-120]. ✓
→ Check: vertex #2 [150,-120] mirrors vertex #12 [-150,-120]. ✓
→ Top center (index 6 area) and bottom center (index 7 [0,200]) are on Y-axis. ✓

COPY THIS HEART EXACTLY if asked for a heart. Don't improvise.

★ SYMMETRIC RECIPES FOR OTHER SHAPES:

CIRCLE (4-point bezier, r=180):
  v: [[0,-180],[180,0],[0,180],[-180,0]]
  i: [[-99,0],[0,-99],[99,0],[0,99]]
  o: [[99,0],[0,99],[-99,0],[0,-99]]

STAR 5-pointed (10 points, outer r=180, inner r=80):
  Outer at angles 270°, 342°, 54°, 126°, 198°
  Inner at angles 306°, 18°, 90°, 162°, 234°
  Use sharp corners: all tangents [0,0]

SHIELD (8 points, symmetric):
  v: [[0,-200],[160,-200],[200,-120],[200,40],[0,220],[-200,40],[-200,-120],[-160,-200]]
  i: [[0,0],[20,0],[0,-30],[0,-60],[-100,0],[0,60],[0,30],[-20,0]]
  o: [[0,0],[-20,0],[0,60],[0,30],[100,0],[0,-30],[0,-60],[20,0]]

★ SYMMETRY CHECKLIST before outputting JSON:
1. Walk through "v" array. For each point [X, Y] with X > 0, is there [-X, Y] somewhere? 
2. Are the center-line points (X=0) at the very top/bottom of the shape?
3. For mirrored vertices, are tangents properly mirrored (X negated)?
4. If drawing with an EVEN number of points, count should be even (14, 16, 12, 8, 10).

❌ CROOKED HEART (subtle but noticeable asymmetry — DO NOT DO THIS):
  Right side: vertex [90, -150] with i=[-40,0], o=[50,0]
  Left side:  vertex [-90, -150] with i=[-50,0], o=[40,0]
  → The X-magnitudes differ (40 vs 50). This makes the heart visibly crooked!
✅ Correct: left vertex i=[-50,0], o=[40,0] → mirror of right's o=[50,0] negated = [-50,0] ✓

FINAL RULE: When in doubt, DRAW ONE HALF, THEN MIRROR IT MATHEMATICALLY.
For each right-side point (X, Y) with tangents i=(ix,iy), o=(ox,oy),
the mirrored left-side point is (-X, Y) with tangents i=(-ox, oy), o=(-ix, iy).
"""

# ─── BOT ───────────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ai_client = OpenAI(
    api_key=ONLYSQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

BOT_USERNAME: str = ""  # filled at startup

# ─── FONT RENDERING ENGINE ─────────────────────────────────────────────────────

def _find_bold_font() -> Optional[str]:
    """Locate a bold TTF font on the system."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf",
        "/system/fonts/DroidSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # Fallback: any bold TTF
    for pattern in ["/usr/share/fonts/**/*.ttf", "/system/fonts/*.ttf"]:
        for p in _glob.glob(pattern, recursive=True):
            if "bold" in p.lower():
                return p
    # Any TTF at all
    for pattern in ["/usr/share/fonts/**/*.ttf", "/system/fonts/*.ttf"]:
        found = _glob.glob(pattern, recursive=True)
        if found:
            return found[0]
    return None


_FONT_PATH: Optional[str] = _find_bold_font()
_FONT_OBJ = None  # lazy-loaded TTFont


def _load_font():
    global _FONT_OBJ
    if _FONT_OBJ is not None:
        return _FONT_OBJ
    if not _FONT_PATH:
        return None
    try:
        from fontTools.ttLib import TTFont
        _FONT_OBJ = TTFont(_FONT_PATH)
        log.info("Font loaded: %s", _FONT_PATH)
        return _FONT_OBJ
    except Exception as e:
        log.warning("fonttools unavailable (%s) — text will be drawn by AI", e)
        return None


def _ttf_contours_to_lottie(text: str, target_height: float) -> Tuple[List[dict], float]:
    """
    Convert a text string to Lottie path contours using a system TTF font.
    Returns (contours, total_advance_width).
    """
    font = _load_font()
    if font is None:
        return [], 0.0

    try:
        from fontTools.pens.recordingPen import RecordingPen
    except ImportError:
        return [], 0.0

    upem: int = font["head"].unitsPerEm
    scale: float = target_height / upem
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"].metrics

    contours: List[dict] = []
    x_cur: float = 0.0

    for char in text:
        code = ord(char)
        if code == 32:  # space
            adv = hmtx.get("space", hmtx.get("uni0020", (upem // 3, 0)))[0]
            x_cur += adv * scale
            continue

        gname = cmap.get(code) or cmap.get(ord(char.upper()))
        if not gname or gname not in glyph_set:
            x_cur += (upem // 2) * scale
            continue

        pen = RecordingPen()
        try:
            glyph_set[gname].draw(pen)
        except Exception:
            x_cur += (upem // 2) * scale
            continue

        adv: float = hmtx.get(gname, (upem // 2, 0))[0] * scale

        v: List = []
        i_t: List = []
        o_t: List = []

        def _flush_contour() -> None:
            if len(v) >= 2:
                contours.append({
                    "c": True,
                    "v": [list(p) for p in v],
                    "i": [list(t) for t in i_t],
                    "o": [list(t) for t in o_t],
                })

        for op_name, args in pen.value:
            if op_name == "moveTo":
                _flush_contour()
                v.clear(); i_t.clear(); o_t.clear()
                px, py = args[0]
                v.append([px * scale + x_cur, -py * scale])
                i_t.append([0.0, 0.0])
                o_t.append([0.0, 0.0])

            elif op_name == "lineTo":
                px, py = args[0]
                v.append([px * scale + x_cur, -py * scale])
                i_t.append([0.0, 0.0])
                o_t.append([0.0, 0.0])

            elif op_name == "qCurveTo":
                pts = list(args)
                off_pts, on_end = pts[:-1], pts[-1]
                if not v:
                    continue
                prev_fx = (v[-1][0] - x_cur) / scale
                prev_fy = -v[-1][1] / scale
                ends: List = []
                for k in range(len(off_pts) - 1):
                    ends.append(((off_pts[k][0] + off_pts[k + 1][0]) / 2,
                                 (off_pts[k][1] + off_pts[k + 1][1]) / 2))
                ends.append(on_end)
                for ctrl, end_pt in zip(off_pts, ends):
                    cx_f, cy_f = ctrl
                    ex_f, ey_f = end_pt
                    out_x = 2 / 3 * (cx_f - prev_fx) * scale
                    out_y = -2 / 3 * (cy_f - prev_fy) * scale
                    in_x = 2 / 3 * (cx_f - ex_f) * scale
                    in_y = -2 / 3 * (cy_f - ey_f) * scale
                    o_t[-1] = [out_x, out_y]
                    v.append([ex_f * scale + x_cur, -ey_f * scale])
                    i_t.append([in_x, in_y])
                    o_t.append([0.0, 0.0])
                    prev_fx, prev_fy = ex_f, ey_f

            elif op_name == "curveTo":
                if not v or len(args) < 3:
                    continue
                cp1, cp2, ep = args[0], args[1], args[-1]
                px, py = v[-1]
                cp1x = cp1[0] * scale + x_cur
                cp1y = -cp1[1] * scale
                cp2x = cp2[0] * scale + x_cur
                cp2y = -cp2[1] * scale
                ex = ep[0] * scale + x_cur
                ey = -ep[1] * scale
                o_t[-1] = [cp1x - px, cp1y - py]
                v.append([ex, ey])
                i_t.append([cp2x - ex, cp2y - ey])
                o_t.append([0.0, 0.0])

            elif op_name in ("closePath", "endPath"):
                _flush_contour()
                v.clear(); i_t.clear(); o_t.clear()

        _flush_contour()
        v.clear(); i_t.clear(); o_t.clear()
        x_cur += adv

    return contours, x_cur


def render_text_as_layer(
    text: str,
    canvas_x: float,
    canvas_y: float,
    target_height: float,
    fill_rgba: List[float],
    stroke_rgba: Optional[List[float]] = None,
    stroke_w: float = 0.0,
    parent_ind: int = 1,
    layer_ind: int = 200,
    op: int = 120,
) -> Optional[dict]:
    """
    Build a complete Lottie shape layer with text rendered as real font bezier paths.
    Returns None if fonttools is unavailable.
    """
    # FIX: was calling ttf_contours_to_lottie (missing underscore)
    contours, total_w = _ttf_contours_to_lottie(text, target_height)
    if not contours:
        return None

    offset_x = -total_w / 2
    offset_y = target_height * 0.28

    shifted: List[dict] = []
    for cont in contours:
        shifted.append({
            "c": cont["c"],
            "v": [[p[0] + offset_x, p[1] + offset_y] for p in cont["v"]],
            "i": cont["i"],
            "o": cont["o"],
        })

    items: List[dict] = [{"ty": "sh", "ks": {"a": 0, "k": s}} for s in shifted]
    items.append({"ty": "fl", "c": {"a": 0, "k": fill_rgba}, "o": {"a": 0, "k": 100}, "r": 1})
    if stroke_rgba:
        items.append({
            "ty": "st", "c": {"a": 0, "k": stroke_rgba},
            "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": stroke_w},
            "lc": 2, "lj": 2,
        })
    items.append({
        "ty": "tr",
        "a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
        "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
        "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0},
    })

    return {
        "ddd": 0, "ty": 4, "ind": layer_ind,
        "nm": f"font_{text[:20]}", "st": 0, "ip": 0, "op": op, "ao": 0,
        "parent": parent_ind,
        "ks": {
            "a": {"a": 0, "k": [0, 0]},
            "p": {"a": 0, "k": [canvas_x, canvas_y]},
            "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100},
        },
        "shapes": [{"ty": "gr", "nm": "font_gr", "it": items}],
    }


def inject_text(lottie: dict) -> dict:
    """
    Post-process: if AI placed a __text__ spec in the JSON,
    replace it with a real font-rendered layer.
    """
    spec = lottie.pop("__text__", None)
    if not spec:
        return lottie

    if isinstance(spec, str):
        spec = {"text": spec}

    text = str(spec.get("text", "")).strip()
    canvas_x = float(spec.get("x", 256))
    canvas_y = float(spec.get("y", 400))
    height = float(spec.get("height", 90))
    fill = spec.get("fill", [1.0, 1.0, 1.0, 1.0])
    stroke = spec.get("stroke", None)
    stroke_w = float(spec.get("stroke_width", 12))

    if not text:
        return lottie

    layers = lottie.setdefault("layers", [])
    existing_inds = {la.get("ind", 0) for la in layers}
    new_ind = (max(existing_inds) + 1) if existing_inds else 200

    parent_ind = next((la["ind"] for la in layers if la.get("ty") == 3), 1)
    op = lottie.get("op", 120)

    layer = render_text_as_layer(
        text=text, canvas_x=canvas_x, canvas_y=canvas_y,
        target_height=height, fill_rgba=fill,
        stroke_rgba=stroke, stroke_w=stroke_w,
        parent_ind=parent_ind, layer_ind=new_ind, op=op,
    )
    if layer:
        log.info("Font text '%s' injected via fonttools (%s)", text, _FONT_PATH)
        lottie["layers"] = [layer] + layers  # text renders on top
    else:
        log.warning("fonttools not available — AI hand-drawn text will be used")

    return lottie


async def create_emoji_pack(tgs_bytes: bytes, user_id: int, title: str) -> str:
    """Upload TGS → create custom emoji pack → return t.me/addemoji link."""
    sticker_file = await bot.upload_sticker_file(
        user_id=user_id,
        sticker=BufferedInputFile(tgs_bytes, filename="emoji.tgs"),
        sticker_format="animated",
    )

    suffix = f"by_{BOT_USERNAME}"
    uid_part = re.sub(r"[^a-z0-9]", "", str(user_id % 100000).lower())
    ts_part = re.sub(r"[^a-z0-9]", "", str(int(time.time()) % 100000))
    pack_name = f"e{uid_part}t{ts_part}_{suffix}"[:64]

    await bot.create_new_sticker_set(
        user_id=user_id,
        name=pack_name,
        title=title[:64],
        stickers=[
            InputSticker(
                sticker=sticker_file.file_id,
                emoji_list=["⭐"],
                format="animated",
            )
        ],
        sticker_type="custom_emoji",
    )

    return f"https://t.me/addemoji/{pack_name}"


def _collect_shape_points(shapes: list) -> List[List[float]]:
    """Recursively collect all static vertex coordinates from shape items."""
    pts: List[List[float]] = []
    for item in shapes:
        ty = item.get("ty")
        if ty == "gr":
            # Group — recurse into "it"
            pts.extend(_collect_shape_points(item.get("it", [])))
        elif ty == "sh":
            # Path shape
            ks = item.get("ks", {})
            k = ks.get("k", ks) if isinstance(ks, dict) else ks
            if isinstance(k, dict):
                for v in k.get("v", []):
                    if isinstance(v, list) and len(v) >= 2:
                        pts.append(v)
        elif ty == "el":
            # Ellipse — center point
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                s = item.get("s", {})
                sk = s.get("k") if isinstance(s, dict) else None
                if isinstance(sk, list) and len(sk) >= 2:
                    hw, hh = sk[0] / 2, sk[1] / 2
                    pts.append([k[0] - hw, k[1] - hh])
                    pts.append([k[0] + hw, k[1] + hh])
                else:
                    pts.append(k)
        elif ty == "rc":
            # Rectangle — center point
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                s = item.get("s", {})
                sk = s.get("k") if isinstance(s, dict) else None
                if isinstance(sk, list) and len(sk) >= 2:
                    hw, hh = sk[0] / 2, sk[1] / 2
                    pts.append([k[0] - hw, k[1] - hh])
                    pts.append([k[0] + hw, k[1] + hh])
                else:
                    pts.append(k)
    return pts


def _shift_shape_points(shapes: list, dx: float, dy: float) -> None:
    """Recursively shift all static vertex/position coordinates in shapes."""
    for item in shapes:
        ty = item.get("ty")
        if ty == "gr":
            _shift_shape_points(item.get("it", []), dx, dy)
        elif ty == "sh":
            ks = item.get("ks", {})
            k = ks.get("k", ks) if isinstance(ks, dict) else ks
            if isinstance(k, dict):
                for v in k.get("v", []):
                    if isinstance(v, list) and len(v) >= 2:
                        v[0] += dx
                        v[1] += dy
        elif ty in ("el", "rc"):
            p = item.get("p", {})
            k = p.get("k") if isinstance(p, dict) else None
            if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                k[0] += dx
                k[1] += dy
        # Also shift gradient start/end points
        elif ty == "gf":
            for key in ("s", "e"):
                obj = item.get(key, {})
                k = obj.get("k") if isinstance(obj, dict) else None
                if isinstance(k, list) and len(k) >= 2 and isinstance(k[0], (int, float)):
                    k[0] += dx
                    k[1] += dy


def recenter_lottie(d: dict) -> dict:
    """Auto-fix centering: detect if all shapes are offset from [0,0] and shift them back.

    The ROOT null at anchor=[256,256] maps local [0,0] to canvas center.
    If the AI placed shapes around [256,256] instead of [0,0],
    this function detects the offset and corrects it.
    """
    layers = d.get("layers", [])

    # Collect ALL shape points from ALL shape layers (ty=4)
    all_pts: List[List[float]] = []
    for layer in layers:
        if layer.get("ty") != 4:
            continue
        shapes = layer.get("shapes", [])
        all_pts.extend(_collect_shape_points(shapes))

    if not all_pts:
        return d

    # Calculate bounding box center
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    # If the center is far from [0,0] (more than 50px), shapes are likely
    # in absolute canvas coordinates — shift them back to local [0,0]
    THRESHOLD = 50.0
    if abs(center_x) < THRESHOLD and abs(center_y) < THRESHOLD:
        log.info("Shapes already centered (center=[%.1f, %.1f])", center_x, center_y)
        return d

    dx = -center_x
    dy = -center_y
    log.warning(
        "Shapes off-center (center=[%.1f, %.1f]). Shifting by [%.1f, %.1f] to re-center.",
        center_x, center_y, dx, dy,
    )

    for layer in layers:
        if layer.get("ty") != 4:
            continue
        _shift_shape_points(layer.get("shapes", []), dx, dy)

    return d


def normalize_lottie(d: dict) -> dict:
    """Force Telegram TGS requirements: 512×512, 60 fps, max 180 frames.

    Strips unknown top-level keys, forces ddd=0 on every layer,
    ensures assets are empty (TGS can't use external resources),
    and clamps op to 180 frames max.
    """
    # 1) Strip any top-level keys Telegram doesn't expect
    allowed_top = {"v", "fr", "ip", "op", "w", "h", "nm", "ddd", "assets", "layers", "markers"}
    for key in list(d.keys()):
        if key not in allowed_top:
            d.pop(key)

    # 2) Force mandatory values (assign, NOT setdefault)
    d["v"] = "5.7.1"
    d["w"] = 512
    d["h"] = 512
    d["fr"] = 60
    d["ip"] = 0
    d["ddd"] = 0
    d["assets"] = []          # TGS doesn't support precomp / image assets
    d.setdefault("nm", "emoji")

    op = min(int(d.get("op", 120)), 180)
    d["op"] = op

    # 3) Fix ROOT null anchor: MUST be [0,0], NOT [256,256]!
    #    With anchor=[0,0] and position=[256,256], the transform is:
    #    canvas = local * scale + [256,256]  →  local [0,0] = canvas center.
    #    With anchor=[256,256] (WRONG), transform becomes identity at scale 100%
    #    and local [0,0] maps to canvas [0,0] = TOP-LEFT corner!
    for layer in d.get("layers", []):
        if layer.get("ty") == 3:  # NULL layer (ROOT)
            ks = layer.get("ks", {})
            a = ks.get("a", {})
            if isinstance(a, dict) and a.get("a", 0) == 0:  # static (not animated)
                old_anchor = a.get("k", [])
                a["k"] = [0, 0]
                if old_anchor != [0, 0]:
                    log.warning("Fixed ROOT anchor: %s → [0, 0]", old_anchor)
            break  # only fix the first NULL layer (ROOT)

    # 4) Sanitise every layer
    for layer in d.get("layers", []):
        layer["ddd"] = 0       # 3D layers are NOT supported — force off
        layer["ao"] = 0
        layer.setdefault("st", 0)
        layer.setdefault("ip", 0)
        if "op" in layer:
            layer["op"] = min(int(layer["op"]), op)
        # Remove unsupported per-layer keys (e.g. expressions)
        for bad_key in ("ef", "hasMask", "masksProperties"):
            layer.pop(bad_key, None)

    return d


def _fix_shape_items(items: list) -> list:
    """Recursively fix broken shape items generated by AI.
    - Converts wrong 'fill' key to proper ty='fl' shape item
    - Ensures every group has ty='tr' as last item
    - Removes unknown keys from shape items
    """
    non_tr = []
    tr_item = None

    for item in items:
        if not isinstance(item, dict):
            continue
        ty = item.get("ty")

        # Fix: AI sometimes outputs {"fill": {...}} instead of {"ty": "fl", ...}
        if ty is None and "fill" in item:
            fill = item["fill"]
            color = fill.get("k", [1, 0, 0, 1]) if isinstance(fill, dict) else [1, 0, 0, 1]
            item = {"ty": "fl", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": 100}, "r": 1}
            ty = "fl"

        # Fix: AI sometimes outputs {"stroke": {...}} instead of {"ty": "st", ...}
        if ty is None and "stroke" in item:
            st = item["stroke"]
            color = st.get("k", [0, 0, 0, 1]) if isinstance(st, dict) else [0, 0, 0, 1]
            width = item.get("strokeWidth", item.get("stroke_width", 12))
            item = {"ty": "st", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": 100},
                    "w": {"a": 0, "k": width}, "lc": 2, "lj": 2}
            ty = "st"

        # Recurse into groups
        if ty == "gr" and "it" in item:
            item["it"] = _fix_shape_items(item["it"])

        if ty == "tr":
            # Ensure tr has all required fields
            item.setdefault("a", {"a": 0, "k": [0, 0]})
            item.setdefault("p", {"a": 0, "k": [0, 0]})
            item.setdefault("s", {"a": 0, "k": [100, 100]})
            item.setdefault("r", {"a": 0, "k": 0})
            item.setdefault("o", {"a": 0, "k": 100})
            item.setdefault("sk", {"a": 0, "k": 0})
            item.setdefault("sa", {"a": 0, "k": 0})
            tr_item = item
        else:
            non_tr.append(item)

    # tr must always be last
    if tr_item is None:
        tr_item = {
            "ty": "tr",
            "a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
            "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0},
        }

    return non_tr + [tr_item]


def fix_lottie_shapes(d: dict) -> dict:
    """Fix all broken shape items in all layers."""
    for layer in d.get("layers", []):
        if layer.get("ty") == 4 and "shapes" in layer:
            layer["shapes"] = _fix_shape_items(layer["shapes"])
    return d


def json_to_tgs(lottie_dict: dict) -> bytes:
    """Convert Lottie dict → gzip-compressed TGS bytes."""
    raw = json.dumps(lottie_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


def extract_json(text: str) -> dict:
    """Extract the first valid JSON object from AI response text."""
    # FIX: was broken — two statements on one line + bad regex
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = re.sub(r"```\s*$", "", text).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find first { ... } block
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("Could not extract valid JSON from AI response")


async def generate_lottie(prompt: str) -> dict:
    """Call VoidAI API and return Lottie JSON dict (with text injected if needed)."""
    # FIX: was *load_font() — stray asterisk, should be _load_font()
    font_hint = (
        f"Font rendering is AVAILABLE (use __text__ field for any Latin text/numbers). "
        f"Font: {os.path.basename(_FONT_PATH)}"
    ) if _FONT_PATH and _load_font() else (
        "Font rendering is UNAVAILABLE — draw text manually using CHARACTER GRID paths."
    )

    user_content = (
        f"Generate a Telegram premium emoji Lottie JSON for: {prompt}\n\n"
        f"{font_hint}\n\n"
        "REQUIREMENTS:\n"
        "- 512×512 canvas, 60fps, op=120\n"
        "- ROOT null layer (ty=3, ind=1) as parent for ALL layers\n"
        "- Minimum 15 shape layers\n"
        "- Drop shadow layer (dark, offset, behind everything)\n"
        "- Dark outline stroke (10-18px) on all major shapes\n"
        "- Pseudo-3D: draw separate face/side/shadow planes with different brightness\n"
        "- Entrance animation (scale pop-in frames 0-28) + idle loop (frames 30-120)\n"
        "- Vivid colors, rim highlight, gradient fills where appropriate\n"
        "- If text/label needed: use __text__ field (METHOD A) + dark bg rect layer\n"
        "- Output ONLY the raw JSON, nothing else."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    # ── Logging: before API call ──────────────────────────────────────────
    sys_len = len(SYSTEM_PROMPT)
    usr_len = len(user_content)
    log.info(
        "[VoidAI] Sending request → model=%s | system_prompt=%d chars | user_msg=%d chars | base_url=%s",
        AI_MODEL, sys_len, usr_len, ai_client.base_url,
    )
    t_start = time.time()

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: ai_client.chat.completions.create(
                model=AI_MODEL,
                messages=messages,
            ),
        )
    except Exception as api_err:
        elapsed = time.time() - t_start
        log.error(
            "[VoidAI] API call FAILED after %.1fs → %s: %s",
            elapsed, type(api_err).__name__, api_err,
        )
        raise

    elapsed = time.time() - t_start

    # ── Logging: after API call ───────────────────────────────────────────
    raw_text = response.choices[0].message.content if response.choices else ""
    finish_reason = response.choices[0].finish_reason if response.choices else "N/A"
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", "?") if usage else "?"
    completion_tokens = getattr(usage, "completion_tokens", "?") if usage else "?"
    total_tokens = getattr(usage, "total_tokens", "?") if usage else "?"

    log.info(
        "[VoidAI] Response received in %.1fs → finish_reason=%s | "
        "tokens: prompt=%s completion=%s total=%s | response_len=%d chars",
        elapsed, finish_reason, prompt_tokens, completion_tokens, total_tokens, len(raw_text),
    )
    log.info("[VoidAI] Response preview (first 300 chars): %s", raw_text[:300])

    if not raw_text:
        raise ValueError(f"VoidAI returned empty response (finish_reason={finish_reason})")

    lottie = extract_json(raw_text)
    log.info(
        "[VoidAI] JSON parsed OK → %d top-level keys, %d layers",
        len(lottie), len(lottie.get("layers", [])),
    )
    lottie = inject_text(lottie)  # replace __text__ with real font paths if present
    return lottie


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Генератор премиум эмодзи</b>\n\n"
        "Опиши что хочешь — я сгенерирую анимированное премиум эмодзи и создам пак прямо в Telegram.\n\n"
        "Примеры:\n"
        "• <code>красное сердце с пульсацией</code>\n"
        "• <code>зелёная галочка draw-on анимация</code>\n"
        "• <code>огонь с языками пламени</code>\n"
        "• <code>золотая звезда sparkle эффект</code>\n"
        "• <code>синяя молния</code>\n\n"
        "Получишь: <code>.json</code> файл + ссылку на пак премиум эмодзи в Telegram.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text)
async def handle_prompt(message: Message) -> None:
    prompt = message.text.strip()
    if not prompt:
        return

    status = await message.answer("Генерирую эмодзи...")

    try:
        # ── Generate Lottie JSON via AI ──────────────────────────────────────
        lottie_dict = await generate_lottie(prompt)

        # ── Validate basic structure ────────────────────────────────────────
        required_keys = {"v", "fr", "ip", "op", "w", "h", "layers"}
        missing = required_keys - lottie_dict.keys()
        if missing:
            await status.edit_text(
                f"ИИ вернул неполный JSON. Отсутствуют поля: {missing}\nПопробуй ещё раз."
            )
            return

        # FIX: normalize_lottie() was NEVER CALLED — this is the main bug!
        # Without this, Lottie may have wrong w/h/fr/op and Telegram rejects it.
        lottie_dict = normalize_lottie(lottie_dict)

        # Fix broken shape items: wrong 'fill'/'stroke' keys, missing 'tr' transforms
        lottie_dict = fix_lottie_shapes(lottie_dict)

        # Auto-fix centering: if AI placed shapes at canvas coords instead of local [0,0]
        lottie_dict = recenter_lottie(lottie_dict)

        # ── Build files ─────────────────────────────────────────────────────
        json_bytes = json.dumps(lottie_dict, ensure_ascii=False, indent=2).encode("utf-8")
        tgs_bytes = json_to_tgs(lottie_dict)

        safe_name = re.sub(r"[^\w\-]", "_", prompt[:40])
        layer_count = len(lottie_dict.get("layers", []))
        duration = lottie_dict.get("op", 0)
        fps = lottie_dict.get("fr", 60)

        # ── Send JSON file ──────────────────────────────────────────────────
        await message.answer_document(
            BufferedInputFile(json_bytes, filename=f"{safe_name}.json"),
            caption=(
                f"<b>Lottie JSON</b>\n"
                f"Запрос: <i>{prompt}</i>\n"
                f"Слоёв: {layer_count} | {duration} фреймов @ {fps}fps"
            ),
            parse_mode=ParseMode.HTML,
        )

        # ── Create premium emoji pack & send link ───────────────────────────
        await status.edit_text("Создаю пак премиум эмодзи в Telegram...")
        pack_title = prompt[:50]
        try:
            pack_link = await create_emoji_pack(tgs_bytes, message.from_user.id, pack_title)
            await status.edit_text(
                f"<b>Готово!</b> Пак премиум эмодзи создан:\n{pack_link}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as pack_err:
            log.warning("Emoji pack creation failed: %s", pack_err)
            # Fallback: send TGS file
            await message.answer_document(
                BufferedInputFile(tgs_bytes, filename=f"{safe_name}.tgs"),
                caption=(
                    f"<b>TGS файл</b> (пак не удалось создать: <code>{pack_err}</code>)\n"
                    f"Размер: {len(tgs_bytes):,} байт"
                ),
                parse_mode=ParseMode.HTML,
            )
            await status.delete()

    except (ValueError, json.JSONDecodeError) as e:
        log.exception("JSON parse error")
        await status.edit_text(
            f"ИИ вернул невалидный JSON: <code>{e}</code>\nПопробуй перефразировать запрос.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.exception("VoidAI / unexpected error")
        # Truncate & escape error message so Telegram doesn't choke on HTML from 502 pages
        err_text = str(e)[:200].replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        try:
            await status.edit_text(f"Ошибка: <code>{err_text}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            await status.edit_text(f"Ошибка API. Попробуй ещё раз через пару минут.")


async def main() -> None:
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    log.info("Starting bot @%s | model=%s", BOT_USERNAME, AI_MODEL)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        log.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass