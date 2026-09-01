from __future__ import annotations

from types import SimpleNamespace

from http_forward import write_response


class _Content:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _Writer:
    def __init__(self):
        self.parts: list[bytes] = []

    def write(self, data: bytes | bytearray) -> None:
        self.parts.append(bytes(data))


async def test_response_writes_headers_once_and_batches_drains():
    writer = _Writer()
    drains = 0

    async def safe_drain(_writer):
        nonlocal drains
        drains += 1

    server = SimpleNamespace(
        io_buffer_size=4,
        write_drain_threshold=1024,
        stats=SimpleNamespace(add_bytes=lambda **_kwargs: None),
        _active_connections={},
        current_rid=lambda: 0,
        _safe_drain=safe_drain,
    )
    response = SimpleNamespace(
        status=200,
        reason="OK",
        headers={"Content-Length": "16", "X-Origin": "test"},
        content=_Content([b"aaaa", b"bbbb", b"cccc", b"dddd"]),
    )

    await write_response(server, writer, response)

    assert writer.parts[0].startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"X-Origin: test\r\n" in writer.parts[0]
    assert b"".join(writer.parts[1:]) == b"aaaabbbbccccdddd"
    assert drains == 1
