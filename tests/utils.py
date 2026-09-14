import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Coroutine, Generator, Optional
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch


class AsyncThread(threading.Thread):
    def __init__(self, f: Coroutine) -> None:
        super().__init__(daemon=True)
        self.f = f

    def run(self) -> None:
        asyncio.run(self.f)


async def _make_async(x: bytes) -> bytes:
    return x


class BaseTestCase(IsolatedAsyncioTestCase):
    _res: Path = Path(__file__).parent / "res"

    def setUp(self) -> None:
        # every endpoint is cached in ctf-attackapi's process-wide cache, which is keyed by URL
        # only -- not by the per-test cache dir -- so it has to be dropped between tests.
        from attackapi.async_api.api import _api_response_cache

        _api_response_cache.clear()

    @classmethod
    @contextmanager
    def patch_endpoints(
        cls, res_dir: Optional[Path] = None
    ) -> Generator[MagicMock, None, None]:
        """
        Patch ``ClientSession.get`` so each request is served from ``<res_dir>/<basename-of-url>``.
        The mocked ``get`` is not bound, so it is called as ``get(url, timeout=...)``.
        """
        directory = res_dir or cls._res

        def _get(url: str, *args: Any, **kwargs: Any) -> MagicMock:
            name = str(url).rstrip("/").rsplit("/", 1)[-1]
            path = directory / name
            cm = MagicMock()
            cm.__aenter__.return_value = cm
            cm.raise_for_status.return_value = None
            cm.read.side_effect = lambda *a, **k: _make_async(path.read_bytes())
            return cm

        with patch("aiohttp.client.ClientSession.get", side_effect=_get) as mock:
            yield mock
