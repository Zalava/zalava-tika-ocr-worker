"""Forward one bounded stdin request to the worker inside the container."""

import json
import os
import signal
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from service import ALLOWED_CONTENT_TYPES, MAX_INPUT_BYTES, MAX_OUTPUT_CHARS, OcrFailure

MAX_HEADER_BYTES = 1024
MAX_RESPONSE_BYTES = MAX_OUTPUT_CHARS * 6 + 1024
DEADLINE_SECONDS = 20


def exchange(source):
    header = source.readline(MAX_HEADER_BYTES + 1)
    if len(header) > MAX_HEADER_BYTES or not header.endswith(b"\n"):
        raise OcrFailure("INVALID_INPUT", "invalid request header")
    try:
        metadata = json.loads(header)
    except (ValueError, RecursionError):
        raise OcrFailure("INVALID_INPUT", "invalid request header") from None
    if not isinstance(metadata, dict):
        raise OcrFailure("INVALID_INPUT", "invalid request header")
    length = metadata.get("length")
    media_type = metadata.get("mediaType")
    languages = metadata.get("languages", "eng")
    if type(length) is not int or length < 1:
        raise OcrFailure("INVALID_INPUT", "invalid request length")
    if length > MAX_INPUT_BYTES:
        raise OcrFailure("INPUT_LIMIT_EXCEEDED", "request exceeds byte limit")
    if not isinstance(media_type, str) or media_type not in ALLOWED_CONTENT_TYPES:
        raise OcrFailure("UNSUPPORTED_MEDIA_TYPE", "only PNG, JPEG, and PDF are accepted")
    if not isinstance(languages, str) or not all(
        language.isascii() and language.isalpha() and len(language) == 3
        for language in languages.split("+")
    ):
        raise OcrFailure("UNSUPPORTED_LANGUAGE", "invalid language list")
    body = source.read(length)
    if len(body) != length:
        raise OcrFailure("INVALID_INPUT", "request body is incomplete")

    port = int(os.environ.get("SEA_OCR_PORT", "8080"))
    request = Request(
        f"http://127.0.0.1:{port}/v1/ocr",
        data=body,
        headers={"Content-Type": media_type, "X-Sea-Ocr-Languages": languages},
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=DEADLINE_SECONDS)
    except HTTPError as error:
        response = error
    with response:
        output = response.read(MAX_RESPONSE_BYTES + 1)
    if len(output) > MAX_RESPONSE_BYTES:
        raise OcrFailure("OUTPUT_LIMIT_EXCEEDED", "OCR response exceeds byte limit")
    return output


def deadline_expired(_signum, _frame):
    raise OcrFailure("DEADLINE_EXCEEDED", "OCR request deadline exceeded")


def main():
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.alarm(DEADLINE_SECONDS)
    try:
        output = exchange(sys.stdin.buffer)
    except OcrFailure as error:
        output = json.dumps({"code": error.code, "message": error.message}).encode("utf-8")
    except TimeoutError:
        output = b'{"code":"DEADLINE_EXCEEDED","message":"OCR request deadline exceeded"}'
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            output = b'{"code":"DEADLINE_EXCEEDED","message":"OCR request deadline exceeded"}'
        else:
            output = b'{"code":"UNAVAILABLE","message":"OCR worker is unavailable"}'
    except OSError:
        output = b'{"code":"UNAVAILABLE","message":"OCR worker is unavailable"}'
    finally:
        signal.alarm(0)
    sys.stdout.buffer.write(output + b"\n")


if __name__ == "__main__":
    main()
