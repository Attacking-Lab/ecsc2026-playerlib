"""
Data models for the ECSC 2026 scoreboard API.

The ECSC gameserver exposes a set of static JSON files under one ``api/`` directory. These
dataclasses mirror the scoreboard files closely, with friendlier field names.

attack.json is not ours: ctf-attackapi's ``atklab`` dialect decodes it, so :class:`AttackInfo`
and :class:`Team` are re-exported from there rather than defined here.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Union

from attackapi.models import AttackInfo as AttackInfo
from attackapi.models import RawFlagIds as RawFlagIds
from attackapi.models import Team as Team
from typing_extensions import TypeAlias

# ---------------------------------------------------------------------------
# scoreboard_current.json
# ---------------------------------------------------------------------------


class GameStateEnum(IntEnum):
    STOPPED = 1
    PAUSED = 2
    RUNNING = 3


@dataclass(frozen=True)
class GameState:
    """
    Current game state, from ``scoreboard_current.json``.

    :attr:`current_round` is the round the game is on now; :attr:`scoreboard_round` is the most
    recent round for which a scoreboard has been published (usually ``current_round - 1``).
    """

    current_round: int
    state: GameStateEnum
    scoreboard_round: int
    current_round_start: Optional[float] = None
    current_round_until: Optional[float] = None
    validity_period: Optional[int] = None
    banned_teams: List[int] = field(default_factory=list)
    frozen: bool = False
    raw: bytes = b""

    @property
    def is_running(self) -> bool:
        return self.state == GameStateEnum.RUNNING


# ---------------------------------------------------------------------------
# scoreboard_round_<tick>.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirstBlood:
    name: str  # team name that scored the first blood
    ts: float  # unix timestamp
    confirmed: bool
    level: int
    payload: Optional[int] = None  # flag store index this first blood was scored on


# attackers/victims are integers, or '?' while the scoreboard is frozen.
Count: TypeAlias = Union[int, str]


@dataclass(frozen=True)
class ServiceInfo:
    """Per-service summary within a scoreboard round."""

    name: str
    attackers: Count
    victims: Count
    first_blood: List[FirstBlood] = field(default_factory=list)
    flag_stores: int = 1
    flag_stores_exploited: int = 0


@dataclass(frozen=True)
class ServiceResult:
    """One team's result for one service in a scoreboard round."""

    off_points: float
    def_points: float
    sla_points: float
    delta_off: float
    delta_def: float
    delta_sla: float
    stolen: int  # flags stolen from this team for this service
    captured: int  # flags this team captured for this service
    delta_stolen: int
    delta_captured: int
    checker_status: str  # "SUCCESS", "OFFLINE", ... (see api_models.CheckerStatus)
    checker_message: Optional[str] = None
    previous_checker_status: List[str] = field(
        default_factory=list
    )  # rounds -1, -2, -3, ...

    @property
    def points(self) -> float:
        return self.off_points + self.def_points + self.sla_points


@dataclass(frozen=True)
class TeamRanking:
    """One team's row in a scoreboard round. :attr:`services` aligns with :attr:`Scoreboard.services`."""

    team_id: int
    rank: int
    points: float
    off_points: float
    def_points: float
    sla_points: float
    delta_off: float
    delta_def: float
    delta_sla: float
    services: List[ServiceResult] = field(default_factory=list)


@dataclass(frozen=True)
class Scoreboard:
    """A full scoreboard round, from ``scoreboard_round_<tick>.json``."""

    round: int
    services: List[ServiceInfo] = field(default_factory=list)
    ranking: List[TeamRanking] = field(default_factory=list)
    raw: bytes = b""

    @property
    def service_names(self) -> List[str]:
        return [service.name for service in self.services]

    def team(self, team_id: int) -> Optional[TeamRanking]:
        """The ranking row for a team ID, or None."""
        for row in self.ranking:
            if row.team_id == team_id:
                return row
        return None

    def top(self, n: int) -> List[TeamRanking]:
        """The top ``n`` teams by rank."""
        return sorted(self.ranking, key=lambda r: r.rank)[:n]

    def result(self, team_id: int, service: str) -> Optional[ServiceResult]:
        """A team's result for a named service in this round."""
        row = self.team(team_id)
        if row is None:
            return None
        try:
            index = self.service_names.index(service)
        except ValueError:
            return None
        if index < len(row.services):
            return row.services[index]
        return None

    def __repr__(self) -> str:
        return f"Scoreboard(round={self.round}, {len(self.ranking)} teams, {len(self.services)} services)"


# ---------------------------------------------------------------------------
# scoreboard_teams.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreboardTeam:
    """Team metadata, from ``scoreboard_teams.json``."""

    id: int
    name: str
    vulnbox: str  # vulnbox IP
    affiliation: str = ""
    website: str = ""
    logo: Union[str, bool] = False  # "<hash>.png" or False if the team has no logo


# ---------------------------------------------------------------------------
# scoreboard_team_<id>.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamHistory:
    """
    Per-team point history, from ``scoreboard_team_<id>.json``.

    :attr:`points` is indexed ``points[service_index][round]``, aligned with :attr:`services`.
    """

    services: List[str] = field(default_factory=list)
    points: List[List[float]] = field(default_factory=list)

    def total_per_round(self) -> List[float]:
        """Summed points across all services, per round."""
        if not self.points:
            return []
        length = min(len(row) for row in self.points)
        return [sum(row[round_] for row in self.points) for round_ in range(length)]


# ---------------------------------------------------------------------------
# scoreboard_service_stats.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceStat:
    attackers: Count
    victims: Count


@dataclass(frozen=True)
class ServiceStats:
    """
    Attacker/victim counts over time, from ``scoreboard_service_stats.json``.

    :attr:`stats` is indexed ``stats[service_index][round]``, aligned with :attr:`services`.
    """

    services: List[str] = field(default_factory=list)
    stats: List[List[ServiceStat]] = field(default_factory=list)
