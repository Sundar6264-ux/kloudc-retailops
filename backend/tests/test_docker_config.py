"""The store image and its compose file agree on where store state lives.

`docker run ... -v storeops-data:/data storeops` (the Dockerfile's own usage
line) has no compose `environment:` block, so every container path compose
pins must also be the image's default, or that store keeps state outside its
volume - and as the non-root user it cannot even create it there.
"""

import pathlib
import re
from typing import Dict

REPO = pathlib.Path(__file__).resolve().parents[2]


def _dockerfile_env() -> Dict[str, str]:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    # Join continuation lines, then read every ENV instruction (KEY=VALUE form).
    joined = re.sub(r"\\\n\s*", " ", text)
    env: Dict[str, str] = {}
    for line in joined.splitlines():
        if line.startswith("ENV "):
            for key, value in re.findall(r"([A-Z_][A-Z0-9_]*)=(\S+)", line[4:]):
                env[key] = value
    return env


def _compose_store_environment() -> Dict[str, str]:
    lines = (REPO / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    env: Dict[str, str] = {}
    in_store = in_env = False
    for line in lines:
        if re.match(r"^  [a-z]", line):
            in_store = line.strip() == "store:"
            in_env = False
            continue
        if not in_store:
            continue
        if re.match(r"^    environment:\s*$", line):
            in_env = True
            continue
        if in_env:
            if re.match(r"^    \S", line):
                in_env = False
                continue
            m = re.match(r"^\s+([A-Z_][A-Z0-9_]*):\s*(\S+)\s*$", line)
            if m:
                env[m.group(1)] = m.group(2)
    return env


def test_compose_pins_are_also_the_image_defaults():
    image = _dockerfile_env()
    compose = _compose_store_environment()
    assert compose, "could not read the store service's environment block"
    mismatched = {k: (v, image.get(k)) for k, v in compose.items() if image.get(k) != v}
    assert not mismatched, f"compose value vs Dockerfile ENV: {mismatched}"


def test_image_keeps_all_store_state_in_the_volume():
    image = _dockerfile_env()
    for key in ("DATABASE_URL", "UPLOADS_ROOT", "INVOICE_UPLOADS_DIR", "TEAM_UPLOADS_DIR", "FLYERS_DIR",
                "INSIGHTS_HMAC_KEY_FILE"):
        assert key in image, f"Dockerfile ENV has no {key}"
        assert "/data/" in image[key], (key, image[key])
    root = image["UPLOADS_ROOT"]
    for key in ("INVOICE_UPLOADS_DIR", "TEAM_UPLOADS_DIR", "FLYERS_DIR"):
        assert image[key].startswith(root + "/"), (key, image[key], root)
