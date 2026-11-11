#!/usr/bin/env sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "usage: $0 <image-reference>" >&2
  exit 64
fi
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro anchore/syft:v1.20.0 "$1" -o spdx-json
