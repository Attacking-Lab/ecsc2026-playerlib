from attackapi.async_api import Decoder

from ecsc2026ad.decoders import (
    parse_game_state,
    parse_scoreboard,
    parse_scoreboard_teams,
    parse_service_stats,
    parse_team_history,
)
from ecsc2026ad.models import GameStateEnum, Team

from .utils import BaseTestCase


class AttackInfoTestCase(BaseTestCase):
    """
    attack.json is decoded by ctf-attackapi's atklab dialect. These pin our fixture -- a copy of
    what the gameserver writes -- against that decoder, so a dialect change cannot silently
    break this library.
    """

    def test_attack_info(self) -> None:
        info = Decoder().parse((self._res / "attack.json").read_bytes())
        self.assertEqual(3, len(info.teams))
        self.assertEqual("ECSC\\{[A-Za-z0-9-_]{32}\\}", info.flag_regex)
        self.assertEqual(7, info.current_round)
        self.assertSetEqual({"ServiceA", "ServiceB"}, info.services)
        # lookup by id, ip, name (case insensitive)
        self.assertEqual(Team(1, "10.32.1.2", "NOP"), info.team_lookup["1"])
        self.assertEqual(info.team("1"), info.team("10.32.1.2"))
        self.assertEqual(info.team("1"), info.team("nop"))

    def test_attack_info_flag_ids(self) -> None:
        info = Decoder().parse((self._res / "attack.json").read_bytes())
        raw = info.flag_ids_raw("ServiceA", "nop")
        self.assertEqual(
            {"5": {"0": "alice", "1": "bob"}, "6": {"0": "carol", "1": None}}, raw
        )
        # same result regardless of how the team is addressed
        self.assertEqual(raw, info.flag_ids_raw("servicea", "1"))
        self.assertEqual(raw, info.flag_ids_raw("ServiceA", "10.32.1.2"))
        nop = info.team("nop")
        assert nop is not None
        self.assertEqual(raw, info.flag_ids_raw("ServiceA", nop))
        # flat drops the nulls and the nesting
        self.assertEqual(["alice", "bob", "carol"], info.flag_ids("ServiceA", "nop"))
        self.assertEqual(["dave", "erin"], info.flag_ids("ServiceA", "10.32.2.2"))
        # unknown service / team
        self.assertIsNone(info.flag_ids_raw("nope", "nop"))
        self.assertEqual([], info.flag_ids("ServiceA", "does-not-exist"))


class DecoderTestCase(BaseTestCase):
    def test_game_state(self) -> None:
        state = parse_game_state((self._res / "scoreboard_current.json").read_bytes())
        self.assertEqual(7, state.current_round)
        self.assertEqual(6, state.scoreboard_round)
        self.assertEqual(GameStateEnum.RUNNING, state.state)
        self.assertTrue(state.is_running)
        self.assertFalse(state.frozen)
        self.assertEqual([3], state.banned_teams)

    def test_scoreboard(self) -> None:
        sb = parse_scoreboard((self._res / "scoreboard_round_6.json").read_bytes())
        self.assertEqual(6, sb.round)
        self.assertEqual(["ServiceA", "ServiceB"], sb.service_names)
        self.assertEqual(2, len(sb.ranking))
        # top team
        top = sb.top(1)[0]
        self.assertEqual(2, top.team_id)
        self.assertEqual(1, top.rank)
        self.assertEqual(100.0, top.points)
        # first blood parsed
        self.assertEqual("Team Two", sb.services[0].first_blood[0].name)
        self.assertTrue(sb.services[0].first_blood[0].confirmed)
        # per team-service lookup
        res = sb.result(1, "ServiceA")
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual("OFFLINE", res.checker_status)
        self.assertEqual("connection refused", res.checker_message)
        self.assertEqual(10, res.stolen)
        self.assertEqual(["OFFLINE", "OFFLINE"], res.previous_checker_status)
        self.assertEqual(25.0, res.points)
        self.assertIsNone(sb.result(999, "ServiceA"))
        self.assertIsNone(sb.result(1, "Nope"))

    def test_scoreboard_teams(self) -> None:
        teams = parse_scoreboard_teams(
            (self._res / "scoreboard_teams.json").read_bytes()
        )
        self.assertEqual(2, len(teams))
        self.assertEqual("10.32.2.2", teams[2].vulnbox)
        self.assertEqual("https://two.example", teams[2].website)
        self.assertEqual("def456.png", teams[2].logo)
        self.assertFalse(teams[1].logo)

    def test_team_history(self) -> None:
        hist = parse_team_history((self._res / "scoreboard_team_2.json").read_bytes())
        self.assertEqual(["ServiceA", "ServiceB"], hist.services)
        self.assertEqual([[0.0, 20.0], [0.0, 20.0]], hist.points)
        self.assertEqual([0.0, 40.0], hist.total_per_round())

    def test_service_stats(self) -> None:
        stats = parse_service_stats(
            (self._res / "scoreboard_service_stats.json").read_bytes()
        )
        self.assertEqual(["ServiceA", "ServiceB"], stats.services)
        self.assertEqual(1, stats.stats[0][1].attackers)
        self.assertEqual(1, stats.stats[0][1].victims)
        self.assertEqual(0, stats.stats[1][0].attackers)
