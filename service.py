#!/usr/bin/env python3
"""Bounded, local-only OCR HTTP worker.

The caller owns authorization, originals and provenance. This process accepts
only request bytes and returns text or a deliberately small failure code.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_PIXELS = 40_000_000
MAX_OUTPUT_CHARS = 100_000
DEADLINE_SECONDS = 15
ALLOWED_CONTENT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}
ALLOWED_LANGUAGES = {"eng", "spa", "cat"}


class OcrFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def image_pixels(path):
    data = path.read_bytes()[:65536]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big") * int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 9 < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            pos += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(data[pos:pos + 2], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(data[pos + 3:pos + 5], "big") * int.from_bytes(data[pos + 5:pos + 7], "big")
            pos += length
    raise OcrFailure("INVALID_INPUT", "image header is invalid or unsupported")


def requested_languages(value):
    languages = set(filter(None, value.split("+"))) if value else {"eng"}
    if not languages or not languages <= ALLOWED_LANGUAGES:
        raise OcrFailure("UNSUPPORTED_LANGUAGE", "requested language is not installed")
    return "+".join(sorted(languages))


def checked_run(command, timeout=DEADLINE_SECONDS):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise OcrFailure("DEADLINE_EXCEEDED", "OCR deadline exceeded") from error
    except subprocess.CalledProcessError as error:
        raise OcrFailure("ENGINE_FAILURE", "OCR engine could not process input") from error


def pdf_pages(path):
    result = checked_run(["pdfinfo", str(path)], timeout=3)
    match = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.MULTILINE)
    if not match:
        raise OcrFailure("INVALID_INPUT", "PDF page count is unavailable")
    pages = int(match.group(1))
    if pages < 1 or pages > MAX_PAGES:
        raise OcrFailure("PAGE_LIMIT_EXCEEDED", "PDF exceeds page limit")
    return pages


def extract(content_type, body, languages):
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise OcrFailure("UNSUPPORTED_MEDIA_TYPE", "only PNG, JPEG, and PDF are accepted")
    if not body:
        raise OcrFailure("INVALID_INPUT", "request body is empty")
    if len(body) > MAX_INPUT_BYTES:
        raise OcrFailure("INPUT_LIMIT_EXCEEDED", "request exceeds byte limit")
    languages = requested_languages(languages)
    with tempfile.TemporaryDirectory(prefix="sea-ocr-") as directory:
        source = os.path.join(directory, "input" + ALLOWED_CONTENT_TYPES[content_type])
        with open(source, "wb") as target:
            target.write(body)
        inputs = []
        if content_type == "application/pdf":
            pages = pdf_pages(source)
            prefix = os.path.join(directory, "page")
            checked_run(["pdftoppm", "-r", "150", "-png", source, prefix])
            inputs = sorted(os.path.join(directory, name) for name in os.listdir(directory) if name.startswith("page-") and name.endswith(".png"))
            if len(inputs) != pages:
                raise OcrFailure("ENGINE_FAILURE", "PDF renderer returned an unexpected page count")
        else:
            inputs = [source]
        text = []
        for item in inputs:
            if image_pixels(__import__("pathlib").Path(item)) > MAX_PIXELS:
                raise OcrFailure("PIXEL_LIMIT_EXCEEDED", "image exceeds decoded pixel limit")
            result = checked_run(["tesseract", item, "stdout", "-l", languages, "--psm", "6"])
            text.append(result.stdout)
            if sum(map(len, text)) > MAX_OUTPUT_CHARS:
                raise OcrFailure("OUTPUT_LIMIT_EXCEEDED", "OCR output exceeds character limit")
        output = "\n".join(text).strip()
        if not output:
            raise OcrFailure("EMPTY_RESULT", "OCR completed without recognized text")
        return {"text": output, "pages": len(inputs), "languages": languages, "engine": "tesseract"}


class Handler(BaseHTTPRequestHandler):
    server_version = "SEA-OCR-Worker/1"

    def log_message(self, format, *args):
        return  # Inputs and OCR output must never enter request logs.

    def reply(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, {"status": "ok"})
        else:
            self.reply(404, {"code": "NOT_FOUND"})

    def do_POST(self):
        if self.path != "/v1/ocr":
            self.reply(404, {"code": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > MAX_INPUT_BYTES:
                raise OcrFailure("INPUT_LIMIT_EXCEEDED", "request exceeds byte limit")
            result = extract(self.headers.get_content_type(), self.rfile.read(length), self.headers.get("X-Sea-Ocr-Languages", "eng"))
            self.reply(200, result)
        except (ValueError, OcrFailure) as error:
            failure = error if isinstance(error, OcrFailure) else OcrFailure("INVALID_INPUT", "Content-Length is invalid")
            status = 413 if failure.code in {"INPUT_LIMIT_EXCEEDED", "PAGE_LIMIT_EXCEEDED", "PIXEL_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED"} else 422
            self.reply(status, {"code": failure.code, "message": failure.message})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("SEA_OCR_PORT", "8080"))), Handler).serve_forever()
