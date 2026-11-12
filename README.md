# Zalava Tika OCR worker

`sea-ocr-worker` runs Tesseract OCR for the Zalava Tika module. It receives document bytes over HTTP and returns extracted text. Zalava owns the original files, access decisions, and provenance.

The sole runtime API is `POST /v1/ocr`, accepting `image/png`, `image/jpeg`, or `application/pdf`. It returns bounded text or a stable failure code. The caller must arrange a private request channel and deny external egress. `--network none` is valid for an air-gapped startup/engine smoke test (queried from inside the container); an integration caller needs a separately restricted private network or equivalent local transport.

The request body is capped at 10 MiB; PDFs at 20 pages; decoded images at 40 million pixels; output at 100,000 characters; and each engine invocation at 15 seconds. Supported languages are `eng`, `spa`, and `cat`, supplied in `X-Sea-Ocr-Languages` as a `+`-separated value. Inputs and results are intentionally never logged.

## Development

```sh
python -m unittest -v
docker build -t zalava-tika-ocr-worker:dev .
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=128m --memory 512m --cpus 1 --pids-limit 64 zalava-tika-ocr-worker:dev
```

Before an immutable image release, generate an SPDX SBOM using `scripts/generate-sbom.sh <image-reference>` and attach it to the release evidence. Native package versions and license evidence must be reviewed from the generated document; no license claim is inferred from this repository alone.

## Release

The tag workflow builds and tests the worker image, checks OCR on an image-only
PDF with container networking disabled, and records a registry digest and an
SPDX SBOM on the matching release. Use the digest in image-reference.txt for
installations. Verify anonymous pull before referencing the image in a public
Zalava module. The image is published at ghcr.io/zalava/zalava-tika-ocr-worker.

## License

[Apache License 2.0](LICENSE).
