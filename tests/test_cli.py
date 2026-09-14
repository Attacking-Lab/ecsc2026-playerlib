import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from typing import List, Tuple

from ecsc2026ad import cli

from .utils import BaseTestCase


class CliTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.configdir = tempfile.TemporaryDirectory()
        self._env_backup = os.environ.get("ECSC_CONFIG_DIR")
        os.environ["ECSC_CONFIG_DIR"] = self.configdir.name

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        self.configdir.cleanup()
        if self._env_backup is None:
            os.environ.pop("ECSC_CONFIG_DIR", None)
        else:
            os.environ["ECSC_CONFIG_DIR"] = self._env_backup

    def _run(self, *argv: str) -> Tuple[int, str, str]:
        full: List[str] = list(argv) + [
            "--url",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        ]
        return self._run_bare(*full)

    def _run_bare(self, *argv: str) -> Tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patch_endpoints():
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.run(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_command_shows_help(self) -> None:
        code = cli.run([])
        self.assertEqual(1, code)

    def test_table_alignment_unaffected_by_color(self) -> None:
        # colouring a cell must not change column widths (ANSI codes are zero-width)
        from ecsc2026ad.cli import _ANSI_RE, Out

        headers = ["STATUS", "CAP", "STL"]

        def render(out: "Out", status: str) -> str:
            buf = io.StringIO()
            with redirect_stdout(buf):
                out.table(headers, [[status, 35, 8]], right=(1, 2))
            return buf.getvalue()

        colored = render(Out(True), Out(True).c("SUCCESS", "green"))
        plain = render(Out(False), "SUCCESS")
        self.assertEqual(plain, _ANSI_RE.sub("", colored))
        # sanity: the CAP header right-aligns with its value
        header_line, data_line = plain.splitlines()[:2]
        self.assertEqual(len(header_line), len(data_line))

    def test_teams_json(self) -> None:
        code, out, _ = self._run("teams", "-j")
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual(3, len(data))
        self.assertEqual({"id", "ip", "name", "affiliation"}, set(data[0].keys()))

    def test_teams_affiliation_comes_from_the_scoreboard(self) -> None:
        # attack.json carries only id/name/ip, so affiliation is joined in from
        # scoreboard_teams.json -- and is blank for a team the scoreboard does not list
        code, out, _ = self._run("teams", "-j")
        self.assertEqual(0, code)
        affiliations = {t["id"]: t["affiliation"] for t in json.loads(out)}
        self.assertEqual({1: "", 2: "Uni X", 3: ""}, affiliations)

    def test_teams_grep(self) -> None:
        code, out, _ = self._run("teams", "-j", "--grep", "two")
        self.assertEqual([2], [t["id"] for t in json.loads(out)])

    def test_teams_table(self) -> None:
        code, out, _ = self._run("teams")
        self.assertEqual(0, code)
        self.assertIn("Team Two", out)
        self.assertIn("NAME", out)  # header

    def test_services(self) -> None:
        code, out, _ = self._run("services", "-j")
        data = json.loads(out)
        self.assertEqual({"ServiceA", "ServiceB"}, set(data["services"]))

    def test_flag_ids_team_flat(self) -> None:
        code, out, _ = self._run("attack-info", "ServiceA", "nop")
        self.assertEqual(0, code)
        self.assertEqual(["alice", "bob", "carol"], out.split())

    def test_flag_ids_team_raw_json(self) -> None:
        code, out, _ = self._run("attack-info", "ServiceA", "nop", "-j", "--raw")
        self.assertEqual(
            {"5": {"0": "alice", "1": "bob"}, "6": {"0": "carol", "1": None}},
            json.loads(out),
        )

    def test_flag_ids_all_teams_json(self) -> None:
        code, out, _ = self._run("attack-info", "ServiceA", "-j")
        data = json.loads(out)
        self.assertEqual(["alice", "bob", "carol"], data["10.32.1.2"])
        self.assertEqual(["dave", "erin"], data["10.32.2.2"])

    def test_flag_ids_unknown_service(self) -> None:
        code, _, err = self._run("attack-info", "Nope", "nop")
        self.assertEqual(3, code)
        self.assertIn("unknown service", err)

    def test_flag_ids_unknown_team(self) -> None:
        # an unresolvable team is a typo, not a team without flag IDs, so say so rather than
        # printing the empty result those two cases would otherwise share
        code, _, err = self._run("attack-info", "ServiceA", "nope")
        self.assertEqual(3, code)
        self.assertIn("unknown team", err)
        self.assertIn("known teams", err)

    def test_status(self) -> None:
        code, out, _ = self._run("status", "-j")
        data = json.loads(out)
        self.assertEqual("RUNNING", data["state"])
        self.assertEqual(6, data["scoreboard_round"])

    def test_scoreboard_json(self) -> None:
        code, out, _ = self._run("scoreboard", "-j")
        data = json.loads(out)
        self.assertEqual(6, data["round"])
        self.assertEqual(2, data["ranking"][0]["team_id"])
        self.assertEqual("Team Two", data["ranking"][0]["name"])

    def test_scoreboard_top_and_service(self) -> None:
        code, out, _ = self._run(
            "scoreboard", "6", "--top", "1", "--service", "ServiceA", "-j"
        )
        data = json.loads(out)
        self.assertEqual(1, len(data["ranking"]))
        self.assertEqual("SUCCESS", data["ranking"][0]["service"]["status"])

    def test_scoreboard_table(self) -> None:
        code, out, _ = self._run("scoreboard")
        self.assertIn("RANK", out)
        self.assertIn("Team Two", out)

    def test_scoreboard_round_flag(self) -> None:
        code, out, _ = self._run("scoreboard", "-r", "6", "-j")
        self.assertEqual(0, code)
        self.assertEqual(6, json.loads(out)["round"])

    def test_scoreboard_short_flags(self) -> None:
        # -n (top), -s (service); combined with -j
        code, out, _ = self._run("scoreboard", "-n", "1", "-s", "ServiceA", "-j")
        data = json.loads(out)
        self.assertEqual(1, len(data["ranking"]))
        self.assertEqual("SUCCESS", data["ranking"][0]["service"]["status"])

    def test_scoreboard_single_team_no_round_shows_history(self) -> None:
        # one team + no round -> team history (via the team api), not a scoreboard row
        code, out, _ = self._run("scoreboard", "-t", "Team Two", "-j")
        data = json.loads(out)
        self.assertEqual(2, data["team_id"])
        self.assertEqual([0.0, 40.0], data["total_per_round"])
        self.assertNotIn("ranking", data)

    def test_scoreboard_single_team_with_round_shows_row(self) -> None:
        # specifying a round keeps the scoreboard-row view
        code, out, _ = self._run("scoreboard", "-t", "Team Two", "-r", "6", "-j")
        rows = json.loads(out)["ranking"]
        self.assertEqual([2], [r["team_id"] for r in rows])

    def test_scoreboard_single_team_with_service_shows_row(self) -> None:
        # -s implies the per-service scoreboard columns, not history
        code, out, _ = self._run("scoreboard", "-t", "Team Two", "-s", "ServiceA", "-j")
        rows = json.loads(out)["ranking"]
        self.assertEqual([2], [r["team_id"] for r in rows])
        self.assertEqual("SUCCESS", rows[0]["service"]["status"])

    def test_scoreboard_multiple_rounds_json(self) -> None:
        code, out, _ = self._run("scoreboard", "-r", "5", "-r", "6", "-j")
        data = json.loads(out)
        self.assertNotIn("ranking", data)  # multi-round uses a "rounds" array
        self.assertEqual([5, 6], [rnd["round"] for rnd in data["rounds"]])
        self.assertEqual([1, 2], [r["team_id"] for r in data["rounds"][0]["ranking"]])
        self.assertEqual([2, 1], [r["team_id"] for r in data["rounds"][1]["ranking"]])

    def test_scoreboard_multiple_rounds_table_has_round_column(self) -> None:
        code, out, _ = self._run("scoreboard", "-r", "5", "-r", "6")
        self.assertIn("ROUND", out)

    def test_scoreboard_rounds_and_team(self) -> None:
        # a team across specific rounds -> one row per round
        code, out, _ = self._run(
            "scoreboard", "-t", "Team Two", "-r", "5", "-r", "6", "-j"
        )
        data = json.loads(out)
        self.assertEqual([2], [r["team_id"] for r in data["rounds"][0]["ranking"]])
        self.assertEqual([2], [r["team_id"] for r in data["rounds"][1]["ranking"]])

    def test_scoreboard_duplicate_round_deduped(self) -> None:
        code, out, _ = self._run("scoreboard", "-r", "6", "-r", "6", "-j")
        data = json.loads(out)
        self.assertEqual(
            6, data["round"]
        )  # deduped to a single round -> single-round shape

    def test_scoreboard_negative_round(self) -> None:
        # scoreboard_round is 6 in the fixtures: -1 = latest, -2 = the one before
        code, out, _ = self._run("scoreboard", "-r", "-1", "-j")
        self.assertEqual(6, json.loads(out)["round"])
        code, out, _ = self._run("scoreboard", "-r", "-2", "-j")
        self.assertEqual(5, json.loads(out)["round"])

    def test_scoreboard_negative_rounds_multiple(self) -> None:
        code, out, _ = self._run("scoreboard", "-r", "-1", "-r", "-2", "-j")
        self.assertEqual([6, 5], [rnd["round"] for rnd in json.loads(out)["rounds"]])

    def test_scoreboard_negative_and_positive_deduped(self) -> None:
        # -1 resolves to 6, so "-r -1 -r 6" collapses to one round after resolution
        code, out, _ = self._run("scoreboard", "-r", "-1", "-r", "6", "-j")
        self.assertEqual(6, json.loads(out)["round"])

    def test_scoreboard_multiple_teams(self) -> None:
        # -t is repeatable; mix a name and an id, rows stay in rank order
        code, out, _ = self._run("scoreboard", "-t", "Team Two", "-t", "1", "-j")
        rows = json.loads(out)["ranking"]
        self.assertEqual([2, 1], [r["team_id"] for r in rows])

    def test_url_short_flag(self) -> None:
        # -u is the short alias for --url (shared option)
        code, out, _ = self._run_bare(
            "services",
            "-j",
            "-u",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self.assertEqual(0, code)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_team_history_by_name(self) -> None:
        code, out, _ = self._run("team", "Team Two", "-j")
        data = json.loads(out)
        self.assertEqual(2, data["team_id"])
        self.assertEqual([0.0, 40.0], data["total_per_round"])

    def test_team_history_by_vulnbox_ip(self) -> None:
        # the teams table leads with the IP, so it has to be a way back in
        code, out, _ = self._run("team", "10.32.2.2", "-j")
        self.assertEqual(0, code)
        self.assertEqual(2, json.loads(out)["team_id"])

    def test_team_history_ambiguous_name(self) -> None:
        # "o" is a substring of both NOP and Team Two, and picking one of them silently is how a
        # player ends up reading the wrong team's history without noticing
        code, _, err = self._run("team", "o")
        self.assertEqual(3, code)
        self.assertIn("ambiguous team", err)
        self.assertIn("NOP", err)
        self.assertIn("Team Two", err)

    def test_team_history_exact_name_beats_a_substring(self) -> None:
        # an exact name is an answer, not one candidate among the substring matches it shares
        code, out, _ = self._run("team", "Team Two", "-j")
        self.assertEqual(0, code)
        self.assertEqual(2, json.loads(out)["team_id"])

    def test_scoreboard_team_filter_keeps_every_match(self) -> None:
        # a filter is not a single-team lookup: several matches behind one --team is the point,
        # so the same "o" that is ambiguous for `team` is simply both rows here
        code, out, _ = self._run("scoreboard", "-t", "o", "-r", "6", "-j")
        self.assertEqual(0, code)
        self.assertEqual({1, 2}, {row["team_id"] for row in json.loads(out)["ranking"]})

    def test_scoreboard_team_filter_by_vulnbox_ip(self) -> None:
        code, out, _ = self._run("scoreboard", "-t", "10.32.2.2", "-r", "6", "-j")
        self.assertEqual(0, code)
        self.assertEqual([2], [row["team_id"] for row in json.loads(out)["ranking"]])

    def test_service_stats(self) -> None:
        code, out, _ = self._run("services", "-j")
        data = json.loads(out)
        self.assertEqual(1, data["services"]["ServiceA"]["attackers"])
        self.assertEqual(0, data["services"]["ServiceB"]["attackers"])

    # ---- -r/--round selection ----

    def test_services_round_selects_round(self) -> None:
        code, out, _ = self._run("services", "-r", "0", "-j")
        data = json.loads(out)
        self.assertEqual(0, data["round"])
        self.assertEqual(0, data["services"]["ServiceA"]["attackers"])

    def test_attack_info_round_filters_flat(self) -> None:
        code, out, _ = self._run("attack-info", "ServiceA", "nop", "-r", "6")
        self.assertEqual(["carol"], out.split())

    def test_attack_info_round_raw_json(self) -> None:
        code, out, _ = self._run(
            "attack-info", "ServiceA", "nop", "-r", "5", "-j", "--raw"
        )
        self.assertEqual({"5": {"0": "alice", "1": "bob"}}, json.loads(out))

    def test_team_round_focuses_round(self) -> None:
        code, out, _ = self._run("team", "Team Two", "-r", "-1", "-j")
        data = json.loads(out)
        self.assertEqual(1, data["round"])
        self.assertEqual(40.0, data["total"])

    # ---- scoreboard delta / first bloods / recent states ----

    def test_scoreboard_delta_points(self) -> None:
        code, out, _ = self._run("scoreboard", "-j")
        self.assertEqual(5.0, json.loads(out)["ranking"][0]["delta"])

    def test_scoreboard_first_bloods(self) -> None:
        code, out, _ = self._run("scoreboard", "-j")
        fb = json.loads(out)["first_bloods"]
        self.assertEqual("Team Two", fb["ServiceA"][0]["name"])

    def test_scoreboard_first_bloods_tbd_for_unblooded_stores(self) -> None:
        # ServiceA has 2 flag stores but only 1 first blood -> the other slot renders TBD
        code, out, _ = self._run("scoreboard")
        line = next(
            ln for ln in out.splitlines() if "ServiceA" in ln and "Team Two" in ln
        )
        self.assertIn("Team Two", line)
        self.assertIn("TBD", line)

    def test_scoreboard_recent_service_states(self) -> None:
        code, out, _ = self._run("scoreboard", "-s", "ServiceA", "-j")
        recent = json.loads(out)["ranking"][0]["service"]["recent"]
        self.assertEqual(
            "SUCCESS", recent[0]
        )  # current status first, then previous rounds

    def test_missing_url_falls_back_to_default_host(self) -> None:
        # with no --url/--host, no ECSC_API, and no host ever configured, commands now use the
        # built-in "default" host instead of erroring
        env_backup = os.environ.pop("ECSC_API", None)
        try:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.patch_endpoints() as mock:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.run(["teams", "-j", "--cache-dir", self.tempdir.name])
            self.assertEqual(0, code)
            self.assertEqual("", stderr.getvalue())
            self.assertTrue(json.loads(stdout.getvalue()))
            self.assertIn(
                "https://scoreboard.ad.ecsc2026.de/api/attack.json",
                [call.args[0] for call in mock.call_args_list],
            )
        finally:
            if env_backup is not None:
                os.environ["ECSC_API"] = env_backup

    def _teams_request_host(self) -> str:
        """Run ``teams`` with no --url/--host and return the host it actually requested from."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patch_endpoints() as mock:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                cli.run(["teams", "-j", "--cache-dir", self.tempdir.name])
        return str(mock.call_args.args[0])

    def test_env_var_beats_the_default_host(self) -> None:
        # the built-in default host exists for a player who configured nothing; an environment
        # naming a game is something configured, so it must not be shadowed by the fallback
        env_backup = os.environ.get("ECSC_API")
        os.environ["ECSC_API"] = "http://from-env/api/"
        try:
            self.assertTrue(
                self._teams_request_host().startswith("http://from-env/api/")
            )
        finally:
            if env_backup is None:
                os.environ.pop("ECSC_API", None)
            else:
                os.environ["ECSC_API"] = env_backup

    def test_selected_host_beats_the_env_var(self) -> None:
        env_backup = os.environ.get("ECSC_API")
        os.environ["ECSC_API"] = "http://from-env/api/"
        try:
            self._run_bare("host", "add", "local", "http://from-host/api/")
            self.assertTrue(
                self._teams_request_host().startswith("http://from-host/api/")
            )
        finally:
            if env_backup is None:
                os.environ.pop("ECSC_API", None)
            else:
                os.environ["ECSC_API"] = env_backup

    # ---- host command group ----

    def test_default_host_preselected(self) -> None:
        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual(0, code)
        data = json.loads(out)
        self.assertEqual("default", data["selected"])
        self.assertEqual(
            {
                "name": "default",
                "url": "https://scoreboard.ad.ecsc2026.de",
                "lifetime": None,
                "timeout": None,
                "cache_dir": None,
            },
            next(h for h in data["hosts"] if h["name"] == "default"),
        )

    def test_default_host_overridden_by_selecting_another(self) -> None:
        self._run_bare("host", "add", "local", "http://localhost/api/", "--no-select")
        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual("default", json.loads(out)["selected"])

        self._run_bare("host", "select", "local")
        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual("local", json.loads(out)["selected"])

    def test_host_add_list_select(self) -> None:
        code, out, _ = self._run_bare("host", "add", "local", "http://localhost/api/")
        self.assertEqual(0, code)
        self.assertIn("selected", out)  # first host auto-selected

        code, out, _ = self._run_bare(
            "host", "add", "other", "http://other/api/", "--no-select"
        )
        self.assertEqual(0, code)

        code, out, _ = self._run_bare("host", "list", "-j")
        data = json.loads(out)
        self.assertEqual("local", data["selected"])
        # "default" is always present alongside whatever the player has saved
        self.assertEqual(
            {"local", "other", "default"}, {h["name"] for h in data["hosts"]}
        )

        code, out, _ = self._run_bare("host", "select", "other")
        self.assertEqual(0, code)
        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual("other", json.loads(out)["selected"])

    def test_host_select_without_name_deselects(self) -> None:
        self._run_bare("host", "add", "local", "http://localhost/api/")
        code, out, _ = self._run_bare("host", "select")
        self.assertEqual(0, code)
        self.assertIn("deselected", out)

        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual("default", json.loads(out)["selected"])

    def test_host_rm_clears_selection(self) -> None:
        self._run_bare("host", "add", "local", "http://localhost/api/")
        code, _, _ = self._run_bare("host", "rm", "local")
        self.assertEqual(0, code)
        code, out, _ = self._run_bare("host", "list", "-j")
        data = json.loads(out)
        # falls back to the built-in default host once the explicitly selected one is gone
        self.assertEqual("default", data["selected"])
        self.assertEqual(["default"], [h["name"] for h in data["hosts"]])

    def test_host_add_rejects_dotted_name(self) -> None:
        # a dotted argument is a URL, so it must not also be usable as a name
        code, _, err = self._run_bare("host", "add", "ecsc.de", "http://localhost/api/")
        self.assertEqual(2, code)
        self.assertIn("invalid host name", err)
        code, out, _ = self._run_bare("host", "list", "-j")
        self.assertEqual(["default"], [h["name"] for h in json.loads(out)["hosts"]])

    def test_host_add_assumes_http_scheme(self) -> None:
        code, _, err = self._run_bare("host", "add", "local", "10.13.37.1:4200")
        self.assertEqual(0, code, err)
        code, _, err = self._run_bare("host", "add", "dev", "localhost:4200")
        self.assertEqual(0, code, err)
        code, out, _ = self._run_bare("host", "list", "-j")
        hosts = {h["name"]: h["url"] for h in json.loads(out)["hosts"]}
        self.assertEqual("http://10.13.37.1:4200", hosts["local"])
        self.assertEqual("http://localhost:4200", hosts["dev"])

    def test_host_flag_assumes_http_scheme(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patch_endpoints() as mock:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.run(
                    [
                        "-H",
                        "10.13.37.1",
                        "services",
                        "-j",
                        "--cache-dir",
                        self.tempdir.name,
                    ]
                )
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual(
            "http://10.13.37.1/api/scoreboard_service_stats.json",
            mock.call_args.args[0],
        )

    def test_unknown_host_flag_is_an_error(self) -> None:
        # not dotted, so it can only have been meant as a saved name
        code, _, err = self._run_bare(
            "services", "-H", "nope", "--cache-dir", self.tempdir.name
        )
        self.assertEqual(3, code)
        self.assertIn("unknown host", err)

    def test_explicit_url_skips_the_host_argument(self) -> None:
        # --url settles the connection on its own, so a stale -H in a shell alias is not consulted
        # and cannot reject the command
        for argv in (
            ("services", "-H", "nope", "-j"),
            ("-H", "nope", "services", "-j"),
        ):
            with self.subTest(argv=argv):
                code, out, err = self._run(*argv)
                self.assertEqual(0, code, err)
                self.assertTrue(json.loads(out))

    def test_host_rm_unknown(self) -> None:
        code, _, err = self._run_bare("host", "rm", "nope")
        self.assertEqual(3, code)
        self.assertIn("unknown host", err)

    def test_host_select_unknown(self) -> None:
        code, _, err = self._run_bare("host", "select", "nope")
        self.assertEqual(3, code)
        self.assertIn("unknown host", err)

    def test_selected_host_used_without_url(self) -> None:
        # a selected host provides base_url (and cache dir) so commands need no --url
        code, _, _ = self._run_bare(
            "host",
            "add",
            "local",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self.assertEqual(0, code)
        code, out, err = self._run_bare("attack-info", "ServiceA", "nop")
        self.assertEqual(0, code, err)
        self.assertEqual(["alice", "bob", "carol"], out.split())

    def test_explicit_host_overrides_selected_host(self) -> None:
        self._run_bare(
            "host",
            "add",
            "bad",
            "http://bad.invalid/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self._run_bare(
            "host",
            "add",
            "local",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
            "--no-select",
        )

        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patch_endpoints() as mock:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.run(["services", "-j", "-H", "local"])

        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual(
            "http://localhost/api/scoreboard_service_stats.json", mock.call_args.args[0]
        )

    def test_explicit_url_overrides_selected_host(self) -> None:
        self._run_bare(
            "host",
            "add",
            "bad",
            "http://unreachable.invalid/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        # explicit --url should win over the selected host
        code, out, _ = self._run("services", "-j")
        self.assertEqual(0, code)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_host_flag_selects_saved_host_by_name(self) -> None:
        self._run_bare(
            "host",
            "add",
            "local",
            "http://localhost/api/",
            "--no-select",
            "--cache-dir",
            self.tempdir.name,
        )
        code, out, err = self._run_bare("services", "-j", "-H", "local")
        self.assertEqual(0, code, err)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_host_flag_accepts_raw_url(self) -> None:
        # -H with a value that isn't a saved host name is used directly as a URL, like --url
        code, out, err = self._run_bare(
            "services",
            "-j",
            "-H",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self.assertEqual(0, code, err)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_host_flag_overrides_selected_host(self) -> None:
        self._run_bare(
            "host",
            "add",
            "bad",
            "http://unreachable.invalid/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        code, out, err = self._run_bare(
            "services",
            "-j",
            "-H",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self.assertEqual(0, code, err)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_group_host_flag_applies_to_subcommand(self) -> None:
        # -H may be given before the subcommand, as a saved name or a raw URL
        self._run_bare(
            "host",
            "add",
            "local",
            "http://localhost/api/",
            "--no-select",
            "--cache-dir",
            self.tempdir.name,
        )
        for value in ("local", "http://localhost/api/"):
            code, out, err = self._run_bare(
                "-H", value, "services", "-j", "--cache-dir", self.tempdir.name
            )
            self.assertEqual(0, code, err)
            self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))

    def test_subcommand_host_flag_overrides_group_host_flag(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.patch_endpoints() as mock:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.run(
                    [
                        "-H",
                        "http://unreachable.invalid/api/",
                        "services",
                        "-j",
                        "-H",
                        "http://localhost/api/",
                        "--cache-dir",
                        self.tempdir.name,
                    ]
                )
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual(
            "http://localhost/api/scoreboard_service_stats.json", mock.call_args.args[0]
        )

    def test_url_overrides_host_flag(self) -> None:
        self._run_bare(
            "host", "add", "bad", "http://unreachable.invalid/api/", "--no-select"
        )
        code, out, err = self._run_bare(
            "services",
            "-j",
            "-H",
            "bad",
            "-u",
            "http://localhost/api/",
            "--cache-dir",
            self.tempdir.name,
        )
        self.assertEqual(0, code, err)
        self.assertEqual({"ServiceA", "ServiceB"}, set(json.loads(out)["services"]))
