"""Contact sheet of screenshots for eyeballing: sheet.py <glob under REG_WORK/shots> <out.png> [cols] [thumb width] [max height ratio]
Each shot is scaled to the thumb width and cut at max-ratio x width (tall full-page shots)."""
import glob, os, sys
from PIL import Image, ImageDraw

work = os.environ.get("REG_WORK", os.path.expanduser("~/.claude/jobs/e26a1611/tmp/suiteA"))
pattern, out = sys.argv[1], sys.argv[2]
cols = int(sys.argv[3]) if len(sys.argv) > 3 else 4
tw = int(sys.argv[4]) if len(sys.argv) > 4 else 420
ratio = float(sys.argv[5]) if len(sys.argv) > 5 else 2.4
files = sorted(glob.glob(os.path.join(work, "shots", pattern)))
if not files:
    sys.exit("no files")
ims = []
for f in files:
    im = Image.open(f).convert("RGB")
    h = int(im.height * tw / im.width)
    im = im.resize((tw, h))
    ims.append((os.path.basename(f), im.crop((0, 0, tw, min(h, int(tw * ratio))))))
th = max(i.height for _, i in ims) + 18
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (tw + 8), rows * th), "white")
d = ImageDraw.Draw(sheet)
for k, (name, im) in enumerate(ims):
    x, y = (k % cols) * (tw + 8), (k // cols) * th
    d.text((x + 2, y + 2), name.replace(".png", ""), fill="black")
    sheet.paste(im, (x, y + 16))
os.makedirs(os.path.join(work, "sheets"), exist_ok=True)
sheet.save(os.path.join(work, "sheets", out))
print(out, sheet.size, len(ims))
