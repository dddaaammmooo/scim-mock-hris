"""Deterministic illustrated SVG avatars, generated locally (no external image service).

The same person always gets the same portrait: features are drawn from a RNG seeded with
their HiBob id. Shirt colour follows their division so teams read as groups on the org chart.
"""
import random
from xml.sax.saxutils import escape

SKIN = ["#ffdbac", "#f9d3b4", "#f1c27d", "#e8b88a", "#e0ac69", "#c68642", "#a8713f", "#8d5524", "#6b4226", "#4a2f1d"]
HAIR = ["#1c1c1c", "#2e2118", "#3f2a1b", "#5a3825", "#7a4a26", "#a0663a", "#c8964f", "#e2c27a", "#9b3b1f"]
GREY = ["#8f8f8f", "#b5b5b5", "#d9d9d9"]
BACKGROUNDS = ["#dbeafe", "#e0e7ff", "#ede9fe", "#fce7f3", "#ffe4e6", "#fef3c7", "#dcfce7", "#ccfbf1", "#e0f2fe", "#f5f5f4"]
SHIRTS = ["#4f46e5", "#0e7490", "#b45309", "#be123c", "#15803d", "#7c3aed", "#1d4ed8", "#c2410c", "#0f766e",
          "#9d174d", "#4d7c0f", "#334155", "#a16207", "#6d28d9", "#0369a1"]


def _shirt(division):
    if not division:
        return SHIRTS[-4]
    return SHIRTS[sum(ord(c) for c in division) % len(SHIRTS)]


def _darken(hex_color, factor=0.85):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(max(0, int(c * factor)) for c in (r, g, b))


def render(user):
    """Return an SVG string for a HiBob-style user record (only id is required)."""
    uid = str(user.get("id") or user.get("email") or "anon")
    rng = random.Random(uid)
    gender = (user.get("personal") or {}).get("gender")
    age = (user.get("personal") or {}).get("age") or rng.randint(25, 55)
    division = ((user.get("work") or {}).get("custom") or {}).get("division")

    bg = rng.choice(BACKGROUNDS)
    skin = rng.choice(SKIN)
    skin_shade = _darken(skin, 0.88)
    grey_chance = 0.05 if age < 40 else 0.3 if age < 52 else 0.65
    hair = rng.choice(GREY) if rng.random() < grey_chance else rng.choice(HAIR)
    shirt = _shirt(division)
    collar = _darken(shirt, 0.75)

    weights = {
        "Female": {"long": 34, "bob": 18, "bun": 14, "curly": 12, "ponytail": 12, "short": 8, "pixie": 2},
        "Male": {"short": 34, "side": 22, "curly": 10, "buzz": 14, "bald": 10, "long": 4, "bun": 6},
    }.get(gender, {"short": 16, "side": 12, "curly": 14, "long": 14, "bob": 14, "bun": 10, "buzz": 10, "pixie": 10})
    style = rng.choices(list(weights), list(weights.values()))[0]
    if style in ("bald",) and age < 30:
        style = "buzz"
    glasses = rng.random() < (0.22 if age < 45 else 0.4)
    beard = gender == "Male" and rng.random() < 0.3
    smile = rng.choice(["M55 77 Q64 84 73 77", "M56 77 Q64 82 72 77", "M57 78 Q64 81 71 78"])
    eye_y = 61

    back, front = "", ""
    if style == "long":
        back = f'<path d="M33 58 C30 30 46 20 64 20 C82 20 98 30 95 58 L99 104 C88 110 40 110 29 104 Z" fill="{hair}"/>'
        front = f'<path d="M37 56 C36 34 49 25 64 25 C80 25 93 34 91 56 C84 44 74 38 62 39 C52 40 44 46 37 56 Z" fill="{hair}"/>'
    elif style == "bob":
        back = f'<path d="M32 60 C29 30 46 21 64 21 C82 21 99 30 96 60 L96 84 C88 88 40 88 32 84 Z" fill="{hair}"/>'
        front = f'<path d="M36 58 C35 33 49 25 64 25 C80 25 93 33 92 58 C90 46 82 40 76 38 C70 44 54 46 40 44 C38 48 37 53 36 58 Z" fill="{hair}"/>'
    elif style == "ponytail":
        back = f'<path d="M86 40 C104 44 106 70 98 92 C94 76 92 60 84 50 Z" fill="{hair}"/>'
        front = f'<path d="M36 56 C35 33 49 24 64 24 C80 24 93 33 92 56 C86 42 76 37 64 37 C52 37 42 42 36 56 Z" fill="{hair}"/>'
    elif style == "bun":
        back = f'<circle cx="64" cy="20" r="12" fill="{hair}"/>'
        front = f'<path d="M36 56 C35 33 49 25 64 25 C80 25 93 33 92 56 C86 42 76 37 64 37 C52 37 42 42 36 56 Z" fill="{hair}"/>'
    elif style == "curly":
        puffs = "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{hair}"/>' for x, y, r in [
            (40, 44, 11), (48, 31, 12), (62, 25, 13), (77, 29, 12), (88, 41, 11), (36, 56, 8), (92, 55, 8)])
        back = puffs
        front = f'<path d="M38 52 C42 40 52 34 64 34 C76 34 86 40 90 52 C84 46 74 43 64 43 C54 43 44 46 38 52 Z" fill="{hair}"/>'
    elif style == "short":
        front = f'<path d="M36 55 C35 31 49 23 64 23 C80 23 93 31 92 55 C88 43 80 37 66 37 C52 37 41 43 36 55 Z" fill="{hair}"/>'
    elif style == "side":
        front = f'<path d="M36 56 C34 31 49 22 66 23 C82 24 94 33 92 56 C90 46 86 40 80 37 C68 41 50 40 42 38 C38 43 37 49 36 56 Z" fill="{hair}"/>'
    elif style == "pixie":
        front = f'<path d="M36 58 C34 32 49 24 64 24 C80 24 94 32 92 52 C84 40 66 46 56 36 C50 46 42 48 36 58 Z" fill="{hair}"/>'
    elif style == "buzz":
        front = f'<path d="M37 52 C37 32 50 26 64 26 C79 26 91 32 91 52 C86 40 76 35 64 35 C52 35 42 40 37 52 Z" fill="{hair}" opacity=".55"/>'
    else:  # bald
        front = (f'<path d="M36 60 C36 52 37 48 39 45 L41 58 Z" fill="{hair}" opacity=".7"/>'
                 f'<path d="M92 60 C92 52 91 48 89 45 L87 58 Z" fill="{hair}" opacity=".7"/>')

    beard_svg = (f'<path d="M40 64 C42 86 52 94 64 94 C76 94 86 86 88 64 C84 76 76 80 64 80 C52 80 44 76 40 64 Z" '
                 f'fill="{hair}" opacity=".9"/>' if beard else "")
    glasses_svg = (f'<g fill="none" stroke="#1f2937" stroke-width="2"><rect x="45" y="{eye_y - 7}" width="15" height="12" rx="4"/>'
                   f'<rect x="68" y="{eye_y - 7}" width="15" height="12" rx="4"/><path d="M60 {eye_y - 2} L68 {eye_y - 2}"/></g>'
                   if glasses else "")
    title = escape((user.get("displayName") or "").strip())

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="{title}">
<title>{title}</title>
<rect width="128" height="128" fill="{bg}"/>
{back}
<path d="M14 128 C17 104 38 94 64 94 C90 94 111 104 114 128 Z" fill="{shirt}"/>
<path d="M52 94 L64 108 L76 94 Z" fill="{collar}"/>
<rect x="55" y="78" width="18" height="20" rx="6" fill="{skin_shade}"/>
<circle cx="37" cy="{eye_y + 2}" r="7" fill="{skin_shade}"/><circle cx="91" cy="{eye_y + 2}" r="7" fill="{skin_shade}"/>
<ellipse cx="64" cy="58" rx="27" ry="31" fill="{skin}"/>
{beard_svg}
<path d="M47 {eye_y - 8} Q53 {eye_y - 11} 58 {eye_y - 8}" stroke="{_darken(hair, 0.8)}" stroke-width="2.4" fill="none" stroke-linecap="round"/>
<path d="M70 {eye_y - 8} Q75 {eye_y - 11} 81 {eye_y - 8}" stroke="{_darken(hair, 0.8)}" stroke-width="2.4" fill="none" stroke-linecap="round"/>
<circle cx="52.5" cy="{eye_y}" r="3" fill="#1f2937"/><circle cx="75.5" cy="{eye_y}" r="3" fill="#1f2937"/>
<path d="M64 63 Q61 70 65 71" stroke="{_darken(skin, 0.75)}" stroke-width="1.8" fill="none" stroke-linecap="round"/>
<path d="{smile}" stroke="#7f1d1d" stroke-width="2.4" fill="none" stroke-linecap="round"/>
{glasses_svg}
{front}
</svg>'''
