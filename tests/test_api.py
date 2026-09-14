import asyncio
import tempfile
import time
from contextlib import contextmanager
from typing import Generator, List, Tuple
from unittest.mock import patch

from ecsc2026ad.api import EcscApiAsync, _normalize_base_url
from ecsc2026ad.models import GameStateEnum

from .utils import AsyncThread, BaseTestCase


class NormalizeBaseUrlTestCase(BaseTestCase):
    def test_normalize(self) -> None:
        self.assertEqual("https://h/api/", _normalize_base_url("https://h/api/"))
        self.assertEqual("https://h/api/", _normalize_base_url("https://h/api"))
        self.assertEqual(
            "https://h/api/", _normalize_base_url("https://h/api/attack.json")
        )
        self.assertEqual(
            "https://h/api/",
            _normalize_base_url("https://h/api/scoreboard_current.json"),
        )

    def test_normalize_appends_api(self) -> None:
        self.assertEqual("https://h/api/", _normalize_base_url("https://h"))
        self.assertEqual("https://h/api/", _normalize_base_url("https://h/"))
        self.assertEqual("https://h:4200/api/", _normalize_base_url("https://h:4200"))
        self.assertEqual("https://u:p@h/api/", _normalize_base_url("https://u:p@h"))
        self.assertEqual("https://h/game/api/", _normalize_base_url("https://h/game"))

    def test_normalize_keeps_file_directory(self) -> None:
        # a URL naming a file says where the API directory is, even at the root
        self.assertEqual("https://h/", _normalize_base_url("https://h/attack.json"))

    def test_normalize_assumes_http_scheme(self) -> None:
        self.assertEqual("http://h.de/api/", _normalize_base_url("h.de"))
        self.assertEqual("http://h.de/api/", _normalize_base_url("h.de/api"))
        self.assertEqual(
            "http://1.2.3.4:4200/api/", _normalize_base_url("1.2.3.4:4200")
        )
        self.assertEqual(
            "http://1.2.3.4/api/", _normalize_base_url("1.2.3.4/api/attack.json")
        )
        self.assertEqual("http://localhost/api/", _normalize_base_url("localhost"))
        self.assertEqual(
            "http://localhost:4200/api/", _normalize_base_url("localhost:4200")
        )


class ApiTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.api = EcscApiAsync("http://localhost/api/", self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            EcscApiAsync("http://localhost/api/", self.tempdir.name, timeout=0.5)
        with self.assertRaises(ValueError):
            EcscApiAsync(
                "http://localhost/api/", self.tempdir.name, lifetime=5, timeout=10
            )

    async def test_fetch_attack_info(self) -> None:
        with self.patch_endpoints() as mock:
            info = await self.api.attack_info()
            mock.assert_called_once()
        self.assertIn("alice", info.flag_ids("ServiceA", "nop"))

    async def test_fetch_scoreboard_resolves_latest_round(self) -> None:
        # scoreboard() with no round reads scoreboard_current.json, then scoreboard_round_6.json
        with self.patch_endpoints() as mock:
            sb = await self.api.scoreboard()
            self.assertEqual(6, sb.round)
            urls = [call.args[0] for call in mock.call_args_list]
        self.assertIn("http://localhost/api/scoreboard_current.json", urls)
        self.assertIn("http://localhost/api/scoreboard_round_6.json", urls)

    async def test_all_endpoints(self) -> None:
        with self.patch_endpoints():
            self.assertEqual(GameStateEnum.RUNNING, (await self.api.game_state()).state)
            self.assertEqual(2, len(await self.api.scoreboard_teams()))
            self.assertEqual(
                ["ServiceA", "ServiceB"], (await self.api.service_stats()).services
            )
            self.assertEqual(
                [0.0, 40.0], (await self.api.team_history(2)).total_per_round()
            )

    async def test_memory_cache(self) -> None:
        with self.patch_endpoints() as mock:
            await self.api.attack_info()
            # a fresh client hitting the same URL uses the shared in-memory cache
            other = EcscApiAsync("http://localhost/api/", self.tempdir.name)
            await other.attack_info()
            mock.assert_called_once()

    async def test_file_cache(self) -> None:
        with self.patch_endpoints() as mock:
            await self.api.attack_info()
            from attackapi.async_api.api import _api_response_cache

            _api_response_cache.clear()
            other = EcscApiAsync("http://localhost/api/", self.tempdir.name)
            await other.attack_info()
            mock.assert_called_once()  # served from disk, no second request

    async def test_progress_wraps_remote_fetch_only(self) -> None:
        events: List[Tuple[str, str]] = []

        @contextmanager
        def progress(url: str) -> Generator[None, None, None]:
            events.append(("start", url))
            try:
                yield
            finally:
                events.append(("stop", url))

        api = EcscApiAsync(
            "http://localhost/api/", self.tempdir.name, progress=progress
        )
        with self.patch_endpoints() as mock:
            await api.game_state()
            await api.game_state()  # cached, no progress
            await api.attack_info()

        self.assertEqual(2, mock.call_count)
        self.assertEqual(
            [
                ("start", "http://localhost/api/scoreboard_current.json"),
                ("stop", "http://localhost/api/scoreboard_current.json"),
                ("start", "http://localhost/api/attack.json"),
                ("stop", "http://localhost/api/attack.json"),
            ],
            events,
        )

    async def test_cache_expires(self) -> None:
        with self.patch_endpoints() as mock:
            await self.api.attack_info()
            with patch("time.time", return_value=time.time() + 120):
                await self.api.attack_info()
            self.assertEqual(2, mock.call_count)

    async def test_distinct_endpoints_cached_separately(self) -> None:
        with self.patch_endpoints() as mock:
            await self.api.attack_info()
            await self.api.game_state()
            await self.api.attack_info()  # cached
            await self.api.game_state()  # cached
            self.assertEqual(2, mock.call_count)

    async def test_simple_concurrency(self) -> None:
        with self.patch_endpoints() as mock:
            await asyncio.gather(*(self.api.attack_info() for _ in range(8)))
            mock.assert_called_once()

    def test_concurrent_threads(self) -> None:
        with self.patch_endpoints() as mock:
            threads = [AsyncThread(self.api.attack_info()) for _ in range(16)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            mock.assert_called_once()
