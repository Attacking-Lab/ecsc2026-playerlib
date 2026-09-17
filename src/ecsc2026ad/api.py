"""
The ECSC 2026 player API clients.

:class:`EcscApiAsync` is the async core; :class:`EcscApiSync` is a thin ``asyncio.run`` wrapper.
Both accept the game's base URL -- the host serving the ``api/`` directory that holds
``attack.json`` and the ``scoreboard_*.json`` files -- and cache every endpoint on memory + disk.
Without one they read ``$ECSC_API``, and without that the ECSC 2026 scoreboard.

Fetching and caching are ctf-attackapi's: every endpoint here is a ``GenericAdCtfApiAsync`` over
the same two-tier (memory + disk) cache. attack.json is decoded there too, by its ``atklab``
dialect; only the scoreboard decoders are ours, since those endpoints are outside its scope.
"""

import asyncio
import os
import tempfile
from contextlib import nullcontext
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ContextManager,
    Coroutine,
    Dict,
    Optional,
    Type,
    TypeVar,
    Union,
)
from urllib.parse import urljoin

from attackapi.async_api import Decoder, GenericAdCtfApiAsync

from ecsc2026ad.decoders import (
    parse_game_state,
    parse_scoreboard,
    parse_scoreboard_teams,
    parse_service_stats,
    parse_team_history,
)
from ecsc2026ad.models import (
    AttackInfo,
    GameState,
    Scoreboard,
    ScoreboardTeam,
    ServiceStats,
    TeamHistory,
)

if TYPE_CHECKING:
    from aiohttp import TCPConnector

ENV_VAR = "ECSC_API"
DEFAULT_API_URL = "https://scoreboard.ad.ecsc2026.de"

T = TypeVar("T")


def _package_version() -> str:
    try:
        return version("ecsc2026ad")
    except PackageNotFoundError:
        return "dev"


def _assume_scheme(url: str) -> str:
    """
    Prepend ``http://`` to a URL given without a scheme, so a bare host or address (``10.13.37.1``,
    ``game.example:4200``) works wherever a URL is accepted. Gameservers are plain HTTP unless the
    player says otherwise, and saying otherwise means writing the scheme.
    """
    if "://" in url:
        return url
    return "http://" + url


def _normalize_base_url(base_url: str) -> str:
    """
    Accept the game's host URL, its ``api/`` directory, or a full URL to one of the files in it,
    and return the directory URL (guaranteed to end with ``/``) so endpoints can be resolved with
    ``urljoin``. A missing scheme is assumed to be ``http://`` (see :func:`_assume_scheme`).

    A URL naming a file is taken at its word -- its directory is the API directory, wherever it
    sits. Anything else is a directory that is expected to *contain* ``api/``, so the segment is
    appended unless it is already there; players configure a host, not a path.
    """
    base_url = _assume_scheme(base_url)
    if base_url.endswith(".json"):
        return base_url.rsplit("/", 1)[0] + "/"
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    if not base_url.endswith("/api/"):
        base_url = base_url + "api/"
    return base_url


class EcscApiAsync:
    """Async client for the ECSC 2026 attack-info and scoreboard APIs, with aggressive caching."""

    def __init__(
        self,
        base_url: str = "",
        tmp_directory: Union[str, Path] = tempfile.gettempdir(),  # noqa: B008
        *,
        lifetime: float = 30.0,
        timeout: float = 10.0,
        aiohttp_arguments: Optional[dict] = None,
        progress: Optional[Callable[[str], ContextManager[None]]] = None,
    ) -> None:
        """
        :param base_url: the game's base URL, its ``api/`` directory, or a file in that
            directory (defaults to the ``ECSC_API`` environment variable, then to the ECSC 2026
            scoreboard). ``api/`` is appended unless the URL already names it, and ``http://`` is
            assumed when no scheme is given. May include basic-auth credentials.
        :param tmp_directory: where to store the disk cache
        :param lifetime: how long to cache data for (seconds)
        :param timeout: how long to wait for a game API request (seconds)
        :param aiohttp_arguments: extra arguments for the aiohttp ``ClientSession``
        :param progress: optional context-manager factory called with the URL during remote fetches
        """
        if not base_url:
            # the production scoreboard as the last resort: during the competition that is the
            # game, so an exploit written without a URL runs instead of raising
            base_url = os.environ.get(ENV_VAR) or DEFAULT_API_URL
        if timeout < 1:
            raise ValueError("Timeout must be at least 1 second")
        if lifetime < timeout:
            raise ValueError("Lifetime must be at least as long as timeout")

        self._base_url = _normalize_base_url(base_url)
        self._cache_dir = Path(tmp_directory)
        self._lifetime = lifetime
        self._timeout = timeout
        self._aiohttp_arguments = aiohttp_arguments or {
            "headers": {"User-Agent": "python/ecsc2026ad " + _package_version()}
        }
        self._progress = progress or (lambda url: nullcontext())
        self._connector: Optional["TCPConnector"] = None
        self._connector_loop: Optional[asyncio.AbstractEventLoop] = None

    async def __aenter__(self) -> "EcscApiAsync":
        """
        Open a connection pool shared by every fetch inside the block, so a round that reads
        attack info and a dozen scoreboard files pays one TCP+TLS handshake instead of a dozen.

        The pool belongs to the event loop that entered it -- which is why :class:`EcscApiSync`
        keeps one loop for the length of its own ``with`` block. Outside a block each fetch opens
        its own session, as before; that is also the only way a fully cached run avoids importing
        aiohttp at all, which is why this is opt-in rather than the default.
        """
        from aiohttp import TCPConnector

        if self._connector is None:
            self._connector = TCPConnector()
            self._connector_loop = asyncio.get_running_loop()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        connector, self._connector = self._connector, None
        self._connector_loop = None
        if connector is not None:
            await connector.close()

    def _aiohttp_kwargs(self) -> Dict[str, Any]:
        """
        Arguments for the session ctf-attackapi opens per fetch, carrying the pooled connector when
        there is one. ``connector_owner`` is what keeps that session from closing a connector the
        next fetch still needs.
        """
        if self._connector is None or "connector" in self._aiohttp_arguments:
            return self._aiohttp_arguments
        try:
            # a connector cannot outlive its loop, and EcscApiSync gives every call a new one
            pooled = self._connector_loop is asyncio.get_running_loop()
        except RuntimeError:
            pooled = False
        if not pooled:
            return self._aiohttp_arguments
        return {
            **self._aiohttp_arguments,
            "connector": self._connector,
            "connector_owner": False,
        }

    def _endpoint(self, filename: str, decoder: Any) -> GenericAdCtfApiAsync:
        return GenericAdCtfApiAsync(
            decoder,
            urljoin(self._base_url, filename),
            self._cache_dir,
            lifetime=self._lifetime,
            timeout=self._timeout,
            aiohttp_arguments=self._aiohttp_kwargs(),
            progress=self._progress,
        )

    async def attack_info(self) -> AttackInfo:
        """Attack info (teams, services, flag IDs) from ``attack.json``."""
        info: AttackInfo = await self._endpoint("attack.json", Decoder()).retrieve()
        return info

    async def game_state(self) -> GameState:
        """Current game state (round, running/stopped, freeze) from ``scoreboard_current.json``."""
        return await self._endpoint(
            "scoreboard_current.json", parse_game_state
        ).retrieve()

    async def scoreboard(self, round: Optional[int] = None) -> Scoreboard:
        """
        A scoreboard round from ``scoreboard_round_<tick>.json``.

        :param round: which round; defaults to the latest published round (``game_state().scoreboard_round``).
        """
        if round is None:
            round = (await self.game_state()).scoreboard_round
        return await self._endpoint(
            f"scoreboard_round_{round}.json", parse_scoreboard
        ).retrieve()

    async def scoreboard_teams(self) -> Dict[int, ScoreboardTeam]:
        """Team metadata keyed by team ID, from ``scoreboard_teams.json``."""
        return await self._endpoint(
            "scoreboard_teams.json", parse_scoreboard_teams
        ).retrieve()

    async def team_history(self, team_id: int) -> TeamHistory:
        """A team's per-service point history from ``scoreboard_team_<id>.json``."""
        return await self._endpoint(
            f"scoreboard_team_{team_id}.json", parse_team_history
        ).retrieve()

    async def service_stats(self) -> ServiceStats:
        """Attacker/victim counts over time from ``scoreboard_service_stats.json``."""
        return await self._endpoint(
            "scoreboard_service_stats.json", parse_service_stats
        ).retrieve()


class EcscApiSync:
    """
    Synchronous client. Each call drives the async client on an event loop of its own, so a single
    call is as cheap as :func:`asyncio.run`.

    Used as a context manager it keeps one loop -- and with it one pooled connection -- for the
    whole block, so a burst of calls (a team's history for every team, say) handshakes once instead
    of once per call. Outside a block nothing is held between calls, which is also what keeps a
    fully cached run from importing aiohttp at all.
    """

    def __init__(
        self,
        base_url: str = "",
        tmp_directory: Union[str, Path] = tempfile.gettempdir(),  # noqa: B008
        **kwargs: Any,
    ) -> None:
        self._api = EcscApiAsync(base_url, tmp_directory, **kwargs)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def __enter__(self) -> "EcscApiSync":
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._api.__aenter__())
        except BaseException:
            loop.close()
            raise
        self._loop = loop
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        loop, self._loop = self._loop, None
        if loop is None:
            return
        try:
            loop.run_until_complete(self._api.__aexit__(exc_type, exc, tb))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()

    def _run(self, coro: Coroutine[Any, Any, T]) -> T:
        if self._loop is None:
            return asyncio.run(coro)
        return self._loop.run_until_complete(coro)

    def attack_info(self) -> AttackInfo:
        return self._run(self._api.attack_info())

    def game_state(self) -> GameState:
        return self._run(self._api.game_state())

    def scoreboard(self, round: Optional[int] = None) -> Scoreboard:
        return self._run(self._api.scoreboard(round))

    def scoreboard_teams(self) -> Dict[int, ScoreboardTeam]:
        return self._run(self._api.scoreboard_teams())

    def team_history(self, team_id: int) -> TeamHistory:
        return self._run(self._api.team_history(team_id))

    def service_stats(self) -> ServiceStats:
        return self._run(self._api.service_stats())
