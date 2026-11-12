#!/usr/bin/env bash
set -euo pipefail

image="${1:?usage: verify-image.sh IMAGE}"
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
container="$(docker run --detach --rm --network none --read-only \
  --tmpfs /tmp:rw,size=128m --memory 512m --cpus 1 --pids-limit 64 \
  --cap-drop ALL --security-opt no-new-privileges "$image")"
trap 'docker rm --force "$container" >/dev/null 2>&1 || true' EXIT

docker exec -i "$container" python -c '
import json
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

for attempt in range(20):
    try:
        with urlopen("http://127.0.0.1:8080/healthz", timeout=2) as response:
            assert json.load(response)["status"] == "ok"
        break
    except URLError:
        if attempt == 19:
            raise
        time.sleep(0.25)

request = Request(
    "http://127.0.0.1:8080/v1/ocr",
    data=sys.stdin.buffer.read(),
    headers={"Content-Type": "application/pdf", "X-Sea-Ocr-Languages": "eng+spa+cat"},
)
with urlopen(request, timeout=20) as response:
    result = json.load(response)
assert "HOUSEHOLD INVOICE 89 EUROS" in " ".join(result["text"].split()), result
assert result["pages"] == 1, result
assert set(result["languages"].split("+")) == {"eng", "spa", "cat"}, result
print("Image-only PDF OCR passed with networking disabled.")
' < "$root/fixtures/scanned-invoice.pdf"
