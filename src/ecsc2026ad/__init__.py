"""
ecsc2026ad -- fast, cached access to the ECSC 2026 attack-info and scoreboard APIs.

Quick start (the client is a context manager -- no global state)::

    from ecsc2026ad import EcscApiSync

    with EcscApiSync("https://scoreboard.ecsc2026ad.example") as ecsc:   # or set ECSC_API
        for team in ecsc.attack_info().teams:
            for flag_id in ecsc.attack_info().flag_ids("service", team):
                pwn(team.ip, flag_id)

Async::

    from ecsc2026ad import EcscApiAsync

    async with EcscApiAsync("https://scoreboard.ecsc2026ad.example") as ecsc:
        info = await ecsc.attack_info()
        board = await ecsc.scoreboard()
"""

from ecsc2026ad.api import EcscApiAsync, EcscApiSync
from ecsc2026ad.models import (
    AttackInfo,
    FirstBlood,
    GameState,
    GameStateEnum,
    Scoreboard,
    ScoreboardTeam,
    ServiceInfo,
    ServiceResult,
    ServiceStat,
    ServiceStats,
    Team,
    TeamHistory,
    TeamRanking,
)

__all__ = [
    # clients (use as context managers)
    "EcscApiAsync",
    "EcscApiSync",
    # models
    "AttackInfo",
    "Team",
    "GameState",
    "GameStateEnum",
    "Scoreboard",
    "TeamRanking",
    "ServiceResult",
    "ServiceInfo",
    "FirstBlood",
    "ScoreboardTeam",
    "TeamHistory",
    "ServiceStats",
    "ServiceStat",
]
