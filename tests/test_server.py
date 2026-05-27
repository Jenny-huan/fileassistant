from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from app.server import AppHandler
from http.server import ThreadingHTTPServer


class ServerTests(unittest.TestCase):
    def test_feishu_status_is_hidden(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as ctx:
                urlopen(f"http://127.0.0.1:{server.server_port}/api/feishu/status", timeout=5)
            self.assertEqual(ctx.exception.code, 404)
            payload = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertEqual(payload["error"], "Not found")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

