import asyncio
import unittest
from unittest.mock import patch

from proxy_server import ProxyServer


class _Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass


class _Content:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, status, headers, chunks=(), reason="OK"):
        self.status = status
        self.reason = reason
        self.headers = headers
        self.content = _Content(chunks)


class ProxyProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.proxy = ProxyServer({"auth_enabled": False, "display_interval": 0})

    async def test_headers_are_case_insensitive(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"hOsT: example.test\r\npRoXy-AuThOrIzAtIoN: Basic abc\r\n\r\n")
        reader.feed_eof()

        headers, _ = await self.proxy._read_headers(reader)

        self.assertEqual(headers["host"], "example.test")
        self.assertEqual(headers["proxy-authorization"], "Basic abc")

    async def test_rejects_duplicate_framing_header(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"Content-Length: 1\r\ncontent-length: 2\r\n\r\n")
        reader.feed_eof()

        with self.assertRaises(ValueError):
            await self.proxy._read_headers(reader)

    def test_hop_by_hop_connection_tokens_are_removed(self):
        names = self.proxy._hop_by_hop_headers({"connection": "keep-alive, X-Internal"})
        self.assertIn("x-internal", names)
        self.assertIn("connection", names)

    async def test_head_response_has_no_chunked_body(self):
        writer = _Writer()
        response = _Response(200, {"Content-Type": "text/plain"}, [b"ignored"])

        await self.proxy._write_response(writer, response, "HEAD")

        wire = bytes(writer.data)
        self.assertNotIn(b"Transfer-Encoding", wire)
        self.assertNotIn(b"ignored", wire)

    async def test_compressed_response_is_forwarded_unchanged(self):
        writer = _Writer()
        compressed = b"not-decoded-on-purpose"
        response = _Response(200, {
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        }, [compressed])

        await self.proxy._write_response(writer, response, "GET")

        wire = bytes(writer.data)
        self.assertIn(b"Content-Encoding: gzip\r\n", wire)
        self.assertIn(f"Content-Length: {len(compressed)}\r\n".encode(), wire)
        self.assertTrue(wire.endswith(compressed))

    async def test_direct_connect_uses_happy_eyeballs(self):
        captured = {}

        class RemoteWriter:
            def get_extra_info(self, _name):
                return None

        async def open_connection(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return asyncio.StreamReader(), RemoteWriter()

        self.proxy.happy_eyeballs_delay = 0.1
        with patch("proxy_server.asyncio.open_connection", open_connection):
            _reader, _writer = await self.proxy._connect_upstream("example.test", 443)

        self.assertEqual(captured["args"], ("example.test", 443))
        self.assertEqual(captured["kwargs"]["happy_eyeballs_delay"], 0.1)
        self.assertEqual(captured["kwargs"]["interleave"], 1)


if __name__ == "__main__":
    unittest.main()
