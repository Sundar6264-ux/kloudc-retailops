"""Contact sheet of screenshots: sheet.py '<glob under shots/>' out.png cols thumb_width"""
import glob
import os
import sys

from PIL import Image, ImageDraw

W = os.environ.get("SUITE_B_WORK", "/tmp/retailops-suiteB")
pattern, out, cols, tw = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
files = sorted(glob.glob(os.path.join(W, "shots", pattern)))
if not files:
    sys.exit("none")
ims = []
for f in files:
    im = Image.open(f).convert("RGB")
    h = int(im.height * tw / im.width)
    ims.append((os.path.basename(f), im.resize((tw, h))))
th = max(i.height for _, i in ims) + 18
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (tw + 6), rows * th), "white")
d = ImageDraw.Draw(sheet)
for k, (name, im) in enumerate(ims):
    x, y = (k % cols) * (tw + 6), (k // cols) * th
    d.text((x + 2, y + 2), name.replace("sweep-", "").replace(".png", ""), fill="black")
    sheet.paste(im, (x, y + 16))
os.makedirs(os.path.join(W, "sheets"), exist_ok=True)
sheet.save(os.path.join(W, "sheets", out))
print(out, sheet.size, len(ims))
