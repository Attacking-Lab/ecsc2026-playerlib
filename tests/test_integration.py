"""
Live integration tests against a real ECSC gameserver.

Skipped by default so the normal offline suite stays green. Enable with::

    ECSC_INTEGRATION=1 uv run pytest tests/test_integration.py -v

Point at a different server with ``ECSC_LIVE_URL`` (defaults to the dev gameserver). If the server
is unreachable while enabled, the tests skip rather than fail.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from ecsc2026ad import EcscApiSync, GameStateEnum, cli

LIVE_URL = os.environ.get("ECSC_LIVE_URL", "http://dev.sinitax.com:4200/api/")
ENABLED = os.environ.get("ECSC_INTEGRATION", "").lower() in ("1", "true", "yes", "on")


@unittest.skipUnless(
    ENABLED, "set ECSC_INTEGRATION=1 to run live tests against a gameserver"
)
class LiveIntegrationTestCase(unittest.TestCase):
    api: EcscApiSync
    tmp: "tempfile.TemporaryDirectory[str]"

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.api = EcscApiSync(LIVE_URL, cls.tmp.name, lifetime=30, timeout=10)
        try:
            cls.api.attack_info()  # reachability probe
        except Exception as e:  # noqa - network
            raise unittest.SkipTest(f"gameserver at {LIVE_URL} unreachable: {e}") from e

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_attack_info(self) -> None:
        info = self.api.attack_info()
        self.assertGreater(len(info.teams), 0)
        self.assertTrue(info.services)
        self.assertIsInstance(info.current_round, int)
        # every team is resolvable by id, ip, and name
        for team in info.teams:
            self.assertIs(info.team(team.id), team)
            self.assertIs(info.team(team.ip), team)
        # flag lookups return well-typed data for every service/team pair
        service = sorted(info.services)[0]
        for team in info.teams:
            self.assertIsInstance(info.flag_ids(service, team), list)

    def test_game_state(self) -> None:
        state = self.api.game_state()
        self.assertIsInstance(state.state, GameStateEnum)
        self.assertGreaterEqual(state.current_round, state.scoreboard_round)

    def test_scoreboard_is_internally_consistent(self) -> None:
        state = self.api.game_state()
        board = self.api.scoreboard()  # latest published round
        self.assertEqual(board.round, state.scoreboard_round)
        # standard competition ranking: tied teams share a rank and the next rank skips, so
        # a team's rank is one more than the number of teams strictly ahead of it
        for row in board.ranking:
            ahead = sum(1 for other in board.ranking if other.rank < row.rank)
            self.assertEqual(row.rank, ahead + 1)
        # and a shared rank is a genuine tie, with rank ordering the teams by points
        by_rank = sorted(board.ranking, key=lambda r: r.rank)
        for index in range(1, len(by_rank)):
            previous, row = by_rank[index - 1], by_rank[index]
            if row.rank == previous.rank:
                self.assertAlmostEqual(row.points, previous.points)
            else:
                self.assertLessEqual(row.points, previous.points)
        # each row carries exactly one result per top-level service, and non-negative points
        for row in board.ranking:
            self.assertEqual(len(row.services), len(board.services))
            self.assertGreaterEqual(row.points, 0.0)

    def test_scoreboard_teams_cover_the_ranking(self) -> None:
        board = self.api.scoreboard()
        teams = self.api.scoreboard_teams()
        self.assertGreater(len(teams), 0)
        for row in board.ranking:
            self.assertIn(row.team_id, teams)
            self.assertTrue(teams[row.team_id].vulnbox)

    def test_service_stats_align_with_scoreboard(self) -> None:
        board = self.api.scoreboard()
        stats = self.api.service_stats()
        self.assertEqual(board.service_names, stats.services)
        # stats is indexed [service_index][round] -> one row per service
        self.assertEqual(len(stats.stats), len(stats.services))

    def test_team_history(self) -> None:
        board = self.api.scoreboard()
        self.assertTrue(board.ranking)
        team_id = board.ranking[0].team_id
        hist = self.api.team_history(team_id)
        self.assertTrue(hist.services)
        self.assertEqual(len(hist.points), len(hist.services))
        self.assertEqual(len(hist.total_per_round()), min(len(r) for r in hist.points))

    def test_cli_status_json(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.run(
                ["status", "-j", "--url", LIVE_URL, "--cache-dir", self.tmp.name]
            )
        self.assertEqual(0, code)
        data = json.loads(out.getvalue())
        self.assertIn("state", data)
        self.assertIn("scoreboard_round", data)

    def test_cli_scoreboard_and_flag_ids(self) -> None:
        info = self.api.attack_info()
        service = sorted(info.services)[0]
        team = self.api.attack_info().teams[0].ip

        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.run(
                ["scoreboard", "-j", "--url", LIVE_URL, "--cache-dir", self.tmp.name]
            )
        self.assertEqual(0, code)
        self.assertIn("ranking", json.loads(out.getvalue()))

        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.run(
                [
                    "attack-info",
                    service,
                    team,
                    "-j",
                    "--url",
                    LIVE_URL,
                    "--cache-dir",
                    self.tmp.name,
                ]
            )
        self.assertEqual(0, code)
        json.loads(
            out.getvalue()
        )  # must be valid JSON (list of flag ids, possibly empty)


if __name__ == "__main__":
    unittest.main()
