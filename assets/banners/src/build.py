"""The banner shown above each theatre's release messages: one design per theatre, in its own colours.

    python3 assets/banners/src/build.py          # writes banners.html next to this file
    node assets/banners/src/render.js            # renders it to assets/banners/<name>.jpg (needs Playwright)

Each banner is 1200x400. The logos in logos/ belong to the theatres; they are used here only to label
that theatre's own ticket news. To change a banner, edit its entry below and render again; Telegram
caches pictures by address, so also bump `?v=` in settings.yaml `messages.banner_url`.
"""

from pathlib import Path

SERIF = "'Liberation Serif','Times New Roman',serif"
SANS = "Inter,'Helvetica Neue',Arial,sans-serif"

B = {}
# Madlenianum: its gold logo on dark walnut, in a wooden frame
B["madlenianum"] = (
    '<div class="b wood"><div class="grain"></div><div class="panel"><div class="grain fine"></div><div class="in">'
    '<img src="logos/madlenianum.png" style="height:262px;margin-top:-6px"></div></div></div>'
)
# Narodno pozorište: the gold seal and lettering on mahogany, in the wooden frame
B["narodno"] = (
    '<div class="b wood"><div class="grain"></div><div class="panel mahogany"><div class="grain fine"></div><div class="in">'
    '<img src="logos/narodno.svg" style="height:246px;filter:drop-shadow(0 3px 5px rgba(0,0,0,.55))">'
    '<div class="txt" style="margin-left:40px"><div class="n1">Народно позориште</div>'
    '<div class="rule"><span>❦</span></div><div class="n2">у Београду · од 1868.</div></div></div></div></div>'
)
# BDP: its flat red, edge to edge, white mark and name (a modern identity: no frame)
B["bdp"] = '<div class="b bdp"><div class="in"><img src="logos/bdp_symbol.png" style="height:190px"><img src="logos/bdp_name.png" style="height:212px;margin-left:56px"></div></div>'
# Sava Centar: its orange with the diagonal split, white logo
B["sava_centar"] = '<div class="b sava"><div class="in"><img src="logos/sava.png" style="height:298px"></div></div>'
# Pozorište na Terazijama: its logo as it is, on warm white
B["terazije"] = '<div class="b terazije"><div class="in"><img src="logos/terazije.png" style="width:1010px"></div></div>'
# Kolarac: the red K tile flush left, the endowment's full name beside it in the same red
B["kolarac"] = (
    '<div class="b kolarac"><img src="logos/kolarac.png" class="tile"><div class="ktext"><div class="k1">ЗАДУЖБИНА<br>ИЛИЈЕ М. КОЛАРЦА</div>'
    '<div class="kline"></div><div class="k2">Београд</div></div></div>'
)
# JDP: the red emblem with a heavy black wordmark on white, as in its own lockup
B["jdp"] = (
    '<div class="b jdp"><div class="in"><img src="logos/jdp_disc.png" style="height:276px">'
    f'<div style="margin-left:50px;font-family:{SANS};color:#111"><div style="font-size:150px;font-weight:900;letter-spacing:-.02em;line-height:.92">JDP</div>'
    '<div style="font-size:26px;font-weight:700;letter-spacing:.13em;margin-top:14px">JUGOSLOVENSKO DRAMSKO POZORIŠTE</div></div></div></div>'
)

CSS = f"""
body {{ margin: 0; background: #777; }}
.b {{ width: 1200px; height: 400px; position: relative; overflow: hidden; box-sizing: border-box; margin-bottom: 10px; }}
.in {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }}
/* the wooden frame (Madlenianum, Narodno) */
.wood {{ padding: 30px; background: linear-gradient(180deg, #9a6537 0%, #7b4a25 22%, #8a5730 50%, #6a3d1c 78%, #5a3216 100%); }}
.grain {{ position: absolute; inset: 0; filter: url(#grain); opacity: .9; }} .grain.fine {{ filter: url(#grain2); opacity: .55; }}
.wood .panel {{ position: relative; height: 100%; border-radius: 6px; overflow: hidden; background: radial-gradient(120% 140% at 50% 40%, #4a2b15 0%, #2e190b 62%, #1f1007 100%);
  box-shadow: 0 0 0 2px #2a1608, 0 0 0 4px #d7b25c, 0 0 0 5px #7a5a22, inset 0 8px 26px rgba(0,0,0,.6), inset 0 -4px 14px rgba(0,0,0,.35); }}
.wood .panel.mahogany {{ background: radial-gradient(120% 140% at 50% 40%, #5a2218 0%, #3a130d 62%, #260b07 100%); }}
.txt {{ font-family: {SERIF}; color: #ecc977; }}
.n1 {{ font-size: 49px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; line-height: 1.1;
  background: linear-gradient(180deg, #fbe6a2 0%, #e2b955 45%, #b98a2c 55%, #f1d27e 100%); -webkit-background-clip: text; background-clip: text; color: transparent;
  filter: drop-shadow(0 2px 2px rgba(0,0,0,.6)); }}
.n2 {{ font-size: 33px; font-style: italic; color: #e3c88c; letter-spacing: .02em; }}
.rule {{ display: flex; align-items: center; gap: 14px; margin: 10px 0 8px; font-size: 24px; color: #d7b25c; }}
.rule:before, .rule:after {{ content: ""; flex: 1; border-top: 2px solid currentColor; opacity: .75; }}
.bdp {{ background: radial-gradient(110% 160% at 50% 45%, #e31333 0%, #d60926 55%, #b9061f 100%); }}
.sava {{ background: linear-gradient(118deg, #f1592a 0%, #f4772c 47.9%, #f8923a 48.1%, #feb244 100%); }}
.terazije {{ background: radial-gradient(130% 150% at 50% 40%, #ffffff 0%, #fbf6f1 75%, #f3e9e2 100%); }}
.kolarac {{ background: linear-gradient(90deg, #f7f2ec 0%, #ffffff 60%); }}
.kolarac .tile {{ position: absolute; left: 0; top: 0; height: 400px; }}
.ktext {{ position: absolute; left: 470px; right: 40px; top: 0; bottom: 0; display: flex; flex-direction: column; justify-content: center; font-family: {SANS}; color: #991b27; }}
.k1 {{ font-size: 58px; font-weight: 800; letter-spacing: .02em; line-height: 1.12; }}
.kline {{ width: 110px; border-top: 6px solid #991b27; margin: 22px 0 16px; }}
.k2 {{ font-size: 30px; font-weight: 500; color: #7a2630; }}
.jdp {{ background: #ffffff; }}
"""
SVG = """<svg width="0" height="0" style="position:absolute"><defs>
<filter id="grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.004 0.19" numOctaves="4" seed="11"/>
<feColorMatrix type="matrix" values="0 0 0 0 0.16  0 0 0 0 0.07  0 0 0 0 0.02  0 0 0 2.6 -1.05"/></filter>
<filter id="grain2" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency="0.006 0.4" numOctaves="3" seed="3"/>
<feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1.8 -0.8"/></filter></defs></svg>"""

if __name__ == "__main__":
    body = "".join(f'<div id="{name}">{html}</div>' for name, html in B.items())
    out = Path(__file__).with_name("banners.html")
    out.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{SVG}{body}</body></html>', encoding="utf-8")
    print("wrote", out, "·", ", ".join(B))
