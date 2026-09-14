import tempfile
from typing import Any, List, Optional, Tuple
from unittest.mock import patch

import aiohttp

from ecsc2026ad import EcscApiAsync, EcscApiSync

from .utils import BaseTestCase


class ContextManagerTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_sync_context_manager(self) -> None:
        with self.patch_endpoints():
            with EcscApiSync("http://localhost/api/", self.tempdir.name) as ecsc:
                info = ecsc.attack_info()
                board = ecsc.scoreboard()
        self.assertEqual(["alice", "bob", "carol"], info.flag_ids("ServiceA", "nop"))
        self.assertEqual(6, board.round)

    async def test_async_context_manager(self) -> None:
        with self.patch_endpoints():
            async with EcscApiAsync("http://localhost/api/", self.tempdir.name) as ecsc:
                info = await ecsc.attack_info()
                teams = await ecsc.scoreboard_teams()
        self.assertEqual(["alice", "bob", "carol"], info.flag_ids("ServiceA", "nop"))
        self.assertEqual("Team Two", teams[2].name)

    def _session_spy(self) -> Tuple[List[Tuple[Any, Any]], Any]:
        """Record the connector each per-fetch ``ClientSession`` is built with."""
        seen: List[Tuple[Any, Any]] = []
        real = aiohttp.ClientSession

        def spy(**kwargs: Any) -> Any:
            seen.append((kwargs.get("connector"), kwargs.get("connector_owner")))
            return real(**kwargs)

        return seen, patch("aiohttp.ClientSession", side_effect=spy)

    async def test_context_manager_pools_connections(self) -> None:
        seen, spy = self._session_spy()
        with self.patch_endpoints(), spy:
            async with EcscApiAsync("http://localhost/api/", self.tempdir.name) as ecsc:
                await ecsc.attack_info()
                await ecsc.scoreboard()  # game state + that round: two more fetches
        self.assertEqual(3, len(seen))
        connectors = {id(c) for c, _ in seen}
        self.assertEqual(1, len(connectors))  # one pool for all three fetches
        connector: Optional[aiohttp.TCPConnector] = seen[0][0]
        self.assertIsNotNone(connector)
        # a session that owned the pool would close it out from under the next fetch
        self.assertEqual({False}, {owner for _, owner in seen})
        assert connector is not None
        self.assertTrue(connector.closed)  # released when the block ended

    async def test_without_context_manager_each_fetch_stands_alone(self) -> None:
        # the plain client holds nothing: no pool to leak, no aiohttp import on a cached run
        seen, spy = self._session_spy()
        with self.patch_endpoints(), spy:
            ecsc = EcscApiAsync("http://localhost/api/", self.tempdir.name)
            await ecsc.attack_info()
            await ecsc.game_state()
        self.assertEqual([(None, None), (None, None)], seen)

    def test_sync_context_manager_pools_connections(self) -> None:
        # one loop for the block means the connector survives between calls, unlike asyncio.run
        seen, spy = self._session_spy()
        with self.patch_endpoints(), spy:
            with EcscApiSync("http://localhost/api/", self.tempdir.name) as ecsc:
                ecsc.attack_info()
                ecsc.game_state()
        self.assertEqual(2, len(seen))
        self.assertEqual(1, len({id(c) for c, _ in seen}))
        self.assertEqual({False}, {owner for _, owner in seen})
        connector: Optional[aiohttp.TCPConnector] = seen[0][0]
        assert connector is not None
        self.assertTrue(connector.closed)

    def test_sync_without_context_manager_each_call_stands_alone(self) -> None:
        seen, spy = self._session_spy()
        with self.patch_endpoints(), spy:
            ecsc = EcscApiSync("http://localhost/api/", self.tempdir.name)
            ecsc.attack_info()
            ecsc.game_state()
        self.assertEqual([(None, None), (None, None)], seen)

    def test_context_manager_returns_self(self) -> None:
        api = EcscApiSync("http://localhost/api/", self.tempdir.name)
        with api as entered:
            self.assertIs(api, entered)
