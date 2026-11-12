import pathlib
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import service


class ServiceBoundsTest(unittest.TestCase):
    def test_defaults_to_english(self):
        self.assertEqual("eng", service.requested_languages(""))

    def test_rejects_uninstalled_language(self):
        with self.assertRaisesRegex(service.OcrFailure, "not installed"):
            service.requested_languages("deu")

    def test_reads_png_dimensions_without_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            image = pathlib.Path(directory, "image.png")
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (400).to_bytes(4, "big") + (300).to_bytes(4, "big"))
            self.assertEqual(120000, service.image_pixels(image))

    def test_rejects_unknown_media_before_engine(self):
        with self.assertRaisesRegex(service.OcrFailure, "only PNG"):
            service.extract("text/plain", b"hello", "eng")

    def test_rejects_empty_and_oversized_input_before_engine(self):
        with self.assertRaisesRegex(service.OcrFailure, "request body is empty"):
            service.extract("image/png", b"", "eng")
        with self.assertRaisesRegex(service.OcrFailure, "request exceeds byte limit"):
            service.extract("image/png", b"x" * (service.MAX_INPUT_BYTES + 1), "eng")

    def test_rejects_pixel_limit_before_tesseract(self):
        image = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (10_000).to_bytes(4, "big") + (10_000).to_bytes(4, "big")
        with self.assertRaisesRegex(service.OcrFailure, "decoded pixel limit"):
            service.extract("image/png", image, "eng")

    def test_maps_engine_timeout_and_output_limit(self):
        image = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (1).to_bytes(4, "big") + (1).to_bytes(4, "big")
        with patch.object(service, "checked_run", side_effect=service.OcrFailure("DEADLINE_EXCEEDED", "OCR deadline exceeded")):
            with self.assertRaisesRegex(service.OcrFailure, "deadline exceeded"):
                service.extract("image/png", image, "eng")
        completed = __import__("subprocess").CompletedProcess([], 0, "x" * (service.MAX_OUTPUT_CHARS + 1), "")
        with patch.object(service, "checked_run", return_value=completed):
            with self.assertRaisesRegex(service.OcrFailure, "output exceeds character limit"):
                service.extract("image/png", image, "eng")


class HttpBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_health_is_available_without_input(self):
        with urllib.request.urlopen(self.base_url + "/healthz") as response:
            self.assertEqual(200, response.status)
            self.assertEqual(b'{"status": "ok"}', response.read())

    def test_rejects_unknown_route_and_media_before_ocr(self):
        with self.assertRaises(urllib.error.HTTPError) as route:
            urllib.request.urlopen(self.base_url + "/unknown")
        self.assertEqual(404, route.exception.code)
        route.exception.close()
        request = urllib.request.Request(
            self.base_url + "/v1/ocr", data=b"untrusted", method="POST",
            headers={"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as media:
            urllib.request.urlopen(request)
        self.assertEqual(422, media.exception.code)
        self.assertIn(b"UNSUPPORTED_MEDIA_TYPE", media.exception.read())
        media.exception.close()


if __name__ == "__main__":
    unittest.main()
