#!/usr/bin/env python3
"""把 record.mjs 抓的帧序列合成为 GIF + 一条横向 filmstrip。

用法: gif.py <framesdir> <out.gif> [--last 2.6] [--fps 14] [--width 900] [--strip out.png] [--strip-n 6]
--last: 取录制末尾 N 秒（动效都发生在触发时刻附近，录制窗口末尾即演出窗口）
"""
import argparse, json, os, sys
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("framesdir")
ap.add_argument("out")
ap.add_argument("--last", type=float, default=2.6)
ap.add_argument("--fps", type=int, default=14)
ap.add_argument("--width", type=int, default=900)
ap.add_argument("--strip")
ap.add_argument("--strip-n", type=int, default=6)
ap.add_argument("--crop", default=None,
                help="裁切比例 l,t,r,b（0-1），如 0.24,0.04,0.99,0.72")
ap.add_argument("--strip-vertical", action="store_true")
ap.add_argument("--webp", action="store_true",
                help="另存同名 .webp 动图（比 GIF 小 10-20 倍，文字类画面尤其明显）")
a = ap.parse_args()

tl = json.load(open(os.path.join(a.framesdir, "timeline.json")))
frames = tl["frames"]
total = frames[-1]["dt"] if frames else 0
lo = max(0.0, total - a.last * 1000)
window = [f for f in frames if f["dt"] >= lo]
if len(window) < 2:
    print("window too small", file=sys.stderr)
    sys.exit(1)

# 按目标 fps 重采样：每个目标时刻取最近的一帧
picked, step = [], 1000.0 / a.fps
t = window[0]["dt"]
while t <= window[-1]["dt"]:
    best = min(window, key=lambda f: abs(f["dt"] - t))
    if not picked or picked[-1]["i"] != best["i"]:
        picked.append(best)
    t += step

crop = tuple(float(x) for x in a.crop.split(",")) if a.crop else None

imgs = []
for f in picked:
    im = Image.open(os.path.join(a.framesdir, f"f{f['i']:04d}.jpg")).convert("RGB")
    if crop:
        W0, H0 = im.size
        im = im.crop((int(crop[0]*W0), int(crop[1]*H0), int(crop[2]*W0), int(crop[3]*H0)))
    w = a.width
    im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
    imgs.append(im)

# GIF（每帧 1000/fps ms）
imgs[0].save(a.out, save_all=True, append_images=imgs[1:],
             duration=int(1000 / a.fps), loop=0, optimize=True)
print(f"gif: {a.out}  frames={len(imgs)}  {imgs[0].size}")

if a.webp:
    wp = a.out.rsplit(".", 1)[0] + ".webp"
    imgs[0].save(wp, save_all=True, append_images=imgs[1:],
                 duration=int(1000 / a.fps), loop=0, quality=72, method=4)
    print(f"webp: {wp}")

if a.strip:
    n = min(a.strip_n, len(imgs))
    idx = [round(i * (len(imgs) - 1) / (n - 1)) for i in range(n)]
    sel = [imgs[i] for i in idx]
    gap = 8
    if a.strip_vertical:
        W = max(s.width for s in sel)
        H = sum(s.height for s in sel) + gap * (n - 1)
        strip = Image.new("RGB", (W, H), (255, 255, 255))
        y = 0
        for s in sel:
            strip.paste(s, (0, y))
            y += s.height + gap
    else:
        W = sum(s.width for s in sel) + gap * (n - 1)
        H = max(s.height for s in sel)
        strip = Image.new("RGB", (W, H), (255, 255, 255))
        x = 0
        for s in sel:
            strip.paste(s, (x, 0))
            x += s.width + gap
    strip.save(a.strip)
    print(f"strip: {a.strip}  {strip.size}")
