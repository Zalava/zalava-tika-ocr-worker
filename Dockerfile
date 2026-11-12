FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-cat \
        tesseract-ocr-eng \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --create-home seaocr
WORKDIR /app
COPY --chown=seaocr:seaocr service.py managed_ocr.py /app/
RUN chmod 0555 /app/service.py /app/managed_ocr.py
USER 10001:10001
EXPOSE 8080
ENV SEA_OCR_PORT=8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['SEA_OCR_PORT'] + '/healthz', timeout=2).close()"]
ENTRYPOINT ["python", "/app/service.py"]
