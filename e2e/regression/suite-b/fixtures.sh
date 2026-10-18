#!/bin/zsh
# Test files for Suite B uploads (made locally with macOS tools; nothing downloaded).
set -e
W=${SUITE_B_WORK:-/Users/sundar/.claude/jobs/e26a1611/tmp/suiteB}
F=$W/fixtures && mkdir -p $F && cd $F
/Users/sundar/spicetown-dev/website/backend/.venv/bin/python -I - <<'PY'
from PIL import Image, ImageDraw
im = Image.new("RGB", (1600, 1200), (250, 236, 210))
d = ImageDraw.Draw(im)
d.rectangle([80, 80, 1520, 1120], outline=(200, 30, 40), width=24)
for i, t in enumerate(["SUITE B TEST FLYER", "Diwali sale - not real", "Spice Town regression"]):
    d.text((160, 200 + i * 160), t, fill=(30, 30, 30))
im.save("flyer.jpg", quality=90)
Image.new("RGB", (900, 700), (40, 120, 200)).save("photo.jpg", quality=85)
PY
sips -s format heic flyer.jpg --out flyer.heic >/dev/null
# A two-page PDF from the JPEG (sips makes a 1-page PDF; that's enough).
sips -s format pdf flyer.jpg --out flyer.pdf >/dev/null
cp flyer.pdf doc.pdf
say -o voice.aiff "Suite B voice note test" && afconvert -f m4af -d aac voice.aiff voice.m4a && rm voice.aiff
ls -la $F
