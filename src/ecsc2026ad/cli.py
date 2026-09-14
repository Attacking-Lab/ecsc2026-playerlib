"""
``ecsc2026ad`` command-line interface (built on Click).

Quickly get and filter the cached ECSC 2026 attack-info and scoreboard data from a shell.
Every command supports ``-j/--json`` for machine-readable output and ``-h/--help``.

Configure the game URL with ``--url``, a saved or raw ``--host``, or the ``ECSC_API`` environment
variable. A URL without a scheme gets ``http://``; a ``--host`` value is a saved name unless it
carries URL punctuation, which is why a name may not contain a period. Without any of those,
commands fall back to the built-in ``"default"`` host, which points at the ECSC 2026 production
scoreboard. All commands share the same on-disk cache as the library, so repeated calls are cheap.
"""

import json
import os
import re
import sys
import threading
from types import TracebackType
from typing import Any, Callable, Dict, List, Optional, Sequence, TextIO, Tuple, Type

import click
from attackapi.models import flatten_flag_ids

from ecsc2026ad.api import ENV_VAR, EcscApiSync, _assume_scheme, _package_version
from ecsc2026ad.api_models import CheckerStatus
from ecsc2026ad.config import Host, HostStore, valid_host_name
from ecsc2026ad.models import ScoreboardTeam

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class RequestSpinner:
    def __init__(self, url: str, stream: Optional[TextIO] = None) -> None:
        self._url = url
        self._stream = stream or sys.stderr
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._width = 0

    def __enter__(self) -> None:
        self._render(0)
        self._thread.start()
        return None

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self._stop.set()
        self._thread.join()
        self._clear()

    def _spin(self) -> None:
        index = 1
        while not self._stop.is_set():
            self._render(index)
            index += 1
            self._stop.wait(0.1)

    def _render(self, index: int) -> None:
        frames = "-\\|/"
        text = f"{frames[index % len(frames)]} requesting {self._url}"
        self._width = max(self._width, len(text))
        self._stream.write("\r" + text)
        self._stream.flush()

    def _clear(self) -> None:
        if self._width:
            self._stream.write("\r" + (" " * self._width) + "\r")
            self._stream.flush()


def _visible_len(text: str) -> int:
    """Length of a string ignoring ANSI colour escapes, for correct column widths."""
    return len(_ANSI_RE.sub("", text))


class Out:
    """Stdout writer that knows whether colour is enabled."""

    def __init__(self, color: bool) -> None:
        self.color = color

    def c(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(_COLORS[s] for s in styles) + text + _COLORS["reset"]

    def table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[Any]],
        right: Sequence[int] = (),
    ) -> None:
        """Render an aligned table. ``right`` lists column indices to right-align."""
        cells = [[("" if v is None else str(v)) for v in row] for row in rows]
        widths = [_visible_len(h) for h in headers]
        for row in cells:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], _visible_len(cell))

        def fmt(values: Sequence[str], style: Sequence[str] = ()) -> str:
            out = []
            for i, value in enumerate(values):
                # pad by *visible* width so ANSI colour codes don't skew alignment
                gap = " " * max(0, widths[i] - _visible_len(value))
                cell = (gap + value) if i in right else (value + gap)
                out.append(self.c(cell, *style) if style else cell)
            return "  ".join(out).rstrip()

        print(fmt([str(h) for h in headers], ("bold",)))
        for row in cells:
            print(fmt(row))
        if not cells:
            print(self.c("(none)", "dim"))


def _emit(json_out: bool, jsonable: Any, render: Callable[[], None]) -> None:
    if json_out:
        print(json.dumps(jsonable, indent=2, ensure_ascii=False))
    else:
        render()


# the gameserver's checker statuses, split by how bad each one is for the team. Anything
# else (RECOVERING, MUMBLE, REVOKED) is neither: the service answered, just not well enough.
_STATUS_OK = frozenset({CheckerStatus.success.value})
_STATUS_BAD = frozenset(
    {
        CheckerStatus.offline.value,
        CheckerStatus.timeout.value,
        CheckerStatus.crashed.value,
    }
)


def _status_style(status: str) -> str:
    if status.upper() in _STATUS_OK:
        return "green"
    if status.upper() in _STATUS_BAD:
        return "red"
    return "yellow"


def _status_color(out: Out, status: str) -> str:
    return out.c(status, _status_style(status))


def _status_glyph(out: Out, status: str) -> str:
    """One-letter coloured tag for a checker status, for compact recent-state trends."""
    letter = (status[:1] or "?").upper()
    return out.c(letter, _status_style(status))


def _delta_colored(out: Out, value: float) -> str:
    text = f"{value:+.1f}"
    if value > 0:
        return out.c(text, "green")
    if value < 0:
        return out.c(text, "red")
    return out.c(text, "dim")


class AmbiguousTeam(Exception):
    """A team argument that names more than one team, where only one will do."""


def _match_teams(value: str, teams: Dict[int, ScoreboardTeam]) -> List[int]:
    """
    Every team an argument matches: an ID as given, then an exact name or vulnbox IP, and only
    failing both a name substring, which may well match several teams.

    IDs are taken at their word rather than checked against ``teams``, which is empty whenever the
    scoreboard is not up yet -- an ID is unambiguous, so there is nothing to resolve.
    """
    if value.isdigit():
        return [int(value)]
    lowered = value.lower()
    exact = [
        tid
        for tid, team in teams.items()
        if team.name.lower() == lowered or team.vulnbox.lower() == lowered
    ]
    if exact:
        return exact
    return [tid for tid, team in teams.items() if lowered in team.name.lower()]


def _resolve_team_id(value: str, teams: Dict[int, ScoreboardTeam]) -> Optional[int]:
    """
    The one team an argument names, or None. A substring matching several teams is a question the
    caller cannot answer for the player, so it raises rather than picking whichever came first.
    """
    matches = _match_teams(value, teams)
    if len(matches) > 1:
        raise AmbiguousTeam(
            f"ambiguous team: {value} matches "
            + ", ".join(f"{teams[tid].name} ({tid})" for tid in sorted(matches))
        )
    return matches[0] if matches else None


def _scoreboard_teams(api: EcscApiSync) -> Dict[int, ScoreboardTeam]:
    """
    Static per-team metadata. attack.json carries only id/name/ip, so anything richer
    (affiliation, logo, website) has to come from the scoreboard.
    """
    try:
        return api.scoreboard_teams()
    except Exception:  # noqa - metadata lookup is best effort (e.g. pre-game)
        return {}


def _resolve_round(round_: Optional[int], length: int) -> int:
    """Resolve a -r/--round value to a 0-based round index (default latest; negatives count back)."""
    if round_ is None:
        return length - 1
    if round_ < 0:
        return length + round_
    return round_


# ---------------------------------------------------------------------------
# command bodies (api + out already built)  ->  exit code
# ---------------------------------------------------------------------------


def _status(api: EcscApiSync, out: Out, json_out: bool) -> int:
    state = api.game_state()
    info = api.attack_info()
    summary = {
        "state": state.state.name,
        "current_round": state.current_round,
        "scoreboard_round": state.scoreboard_round,
        "frozen": state.frozen,
        "validity_period": state.validity_period,
        "banned_teams": state.banned_teams,
        "flag_regex": info.flag_regex,
        "services": sorted(info.services),
        "team_count": len(info.teams),
    }

    def render() -> None:
        colored = state.state.name
        if state.is_running:
            colored = out.c(colored, "green")
        elif state.state.value == 1:
            colored = out.c(colored, "red")
        else:
            colored = out.c(colored, "yellow")
        out.table(
            ["FIELD", "VALUE"],
            [
                ["state", colored],
                ["current round", state.current_round],
                ["scoreboard round", state.scoreboard_round],
                ["frozen", "yes" if state.frozen else "no"],
                ["banned teams", ", ".join(map(str, state.banned_teams)) or "-"],
                ["flag regex", info.flag_regex],
                ["services", ", ".join(sorted(info.services))],
                ["teams", len(info.teams)],
            ],
        )

    _emit(json_out, summary, render)
    return 0


def _teams(api: EcscApiSync, out: Out, json_out: bool, grep: Optional[str]) -> int:
    info = api.attack_info()
    meta = _scoreboard_teams(api)

    def affiliation(team_id: int) -> str:
        entry = meta.get(team_id)
        return entry.affiliation if entry is not None else ""

    teams = info.teams
    if grep:
        needle = grep.lower()
        teams = [
            t
            for t in teams
            if needle in (t.name or "").lower()
            or needle in affiliation(t.id).lower()
            or needle in t.ip
        ]
    teams = sorted(teams, key=lambda t: t.id)
    rows = [dict(t.to_dict(), affiliation=affiliation(t.id)) for t in teams]
    _emit(
        json_out,
        rows,
        lambda: out.table(
            ["ID", "IP", "NAME", "AFFILIATION"],
            [[t.id, t.ip, t.name, affiliation(t.id)] for t in teams],
            right=(0,),
        ),
    )
    return 0


def _services(api: EcscApiSync, out: Out, json_out: bool, round_: Optional[int]) -> int:
    stats = api.service_stats()
    length = min((len(row) for row in stats.stats), default=0)
    which = _resolve_round(round_, length)
    jsonable: Dict[str, Any] = {"round": which, "services": {}}
    display = []
    for i, name in enumerate(stats.services):
        cell = stats.stats[i][which] if 0 <= which < len(stats.stats[i]) else None
        a = cell.attackers if cell else "-"
        v = cell.victims if cell else "-"
        jsonable["services"][name] = {"attackers": a, "victims": v}
        display.append([name, a, v])

    def render() -> None:
        print(out.c(f"service stats @ round {which}", "dim"))
        out.table(["SERVICE", "ATTACKERS", "VICTIMS"], display, right=(1, 2))

    _emit(json_out, jsonable, render)
    return 0


def _flag_ids(
    api: EcscApiSync,
    out: Out,
    json_out: bool,
    service: str,
    team: Optional[str],
    raw: bool,
    round_: Optional[int],
) -> int:
    info = api.attack_info()
    if not info.has_service(service):
        print(out.c(f"unknown service: {service}", "red"), file=sys.stderr)
        print("known services: " + ", ".join(sorted(info.services)), file=sys.stderr)
        return 3

    if team is not None and info.team(team) is None:
        print(out.c(f"unknown team: {team}", "red"), file=sys.stderr)
        print(
            "known teams: " + ", ".join(f"{t.name} ({t.ip})" for t in info.teams),
            file=sys.stderr,
        )
        return 3

    if team is not None:
        raw_ids = info.flag_ids_raw(service, team, round_)
        flat = flatten_flag_ids(raw_ids)
        if json_out:
            print(json.dumps(raw_ids if raw else flat, indent=2, ensure_ascii=False))
        elif raw:
            print(json.dumps(raw_ids, indent=2, ensure_ascii=False))
        else:
            print("\n".join(flat))
        return 0

    teams = info.teams
    result: Dict[str, Any] = {}
    lines: List[str] = []
    for t in sorted(teams, key=lambda t: t.id):
        raw_ids = info.flag_ids_raw(service, t, round_)
        if raw_ids is None:
            continue
        flat = flatten_flag_ids(raw_ids)
        result[t.ip] = raw_ids if raw else flat
        lines.append(f"{out.c(t.ip, 'cyan')}\t{' '.join(flat)}")
    _emit(
        json_out,
        result,
        lambda: print("\n".join(lines) if lines else out.c("(none)", "dim")),
    )
    return 0


def _scoreboard(
    api: EcscApiSync,
    out: Out,
    json_out: bool,
    round_: Optional[int],
    rounds: Tuple[int, ...],
    top: Optional[int],
    teams: Tuple[str, ...],
    service: Optional[str],
) -> int:
    # a single team with no specific round is really "show this team over time"
    if not rounds and round_ is None and service is None and len(teams) == 1:
        return _team_history(api, out, json_out, teams[0], None)

    # which rounds to show: explicit -r (repeatable) > positional round > latest
    if rounds:
        selected: List[Optional[int]] = list(rounds)
    elif round_ is not None:
        selected = [round_]
    else:
        selected = [None]
    # negative rounds are python-style indices from the last published round (-1 = latest)
    if any(r is not None and r < 0 for r in selected):
        latest = api.game_state().scoreboard_round
        selected = [
            latest + r + 1 if (r is not None and r < 0) else r for r in selected
        ]
    selected = list(dict.fromkeys(selected))  # dedupe, preserving order
    multi = len(selected) > 1

    meta = _scoreboard_teams(api)
    names = {tid: t.name for tid, t in meta.items()}

    def filtered(board: Any) -> List[Any]:
        rows = sorted(board.ranking, key=lambda r: r.rank)
        if teams:
            # a filter wants every match, so several teams behind one --team is the point here
            wanted_ids = {rid for t in teams for rid in _match_teams(t, meta)}
            rows = [r for r in rows if r.team_id in wanted_ids]
        if top is not None:
            rows = rows[:top]
        return rows

    per_round = [
        (board, filtered(board)) for board in (api.scoreboard(r) for r in selected)
    ]

    if service is not None:
        known = {name for board, _ in per_round for name in board.service_names}
        if service not in known:
            print(out.c(f"unknown service: {service}", "red"), file=sys.stderr)
            print("known services: " + ", ".join(sorted(known)), file=sys.stderr)
            return 3

    def svc_result(board: Any, r: Any) -> Optional[Any]:
        if service is None or service not in board.service_names:
            return None
        index = board.service_names.index(service)
        return r.services[index] if index < len(r.services) else None

    def row_dict(board: Any, r: Any) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "rank": r.rank,
            "team_id": r.team_id,
            "name": names.get(r.team_id),
            "points": r.points,
            "delta": r.delta_off + r.delta_def + r.delta_sla,
            "off": r.off_points,
            "def": r.def_points,
            "sla": r.sla_points,
            "delta_off": r.delta_off,
            "delta_def": r.delta_def,
            "delta_sla": r.delta_sla,
        }
        sr = svc_result(board, r)
        if sr is not None:
            d["service"] = {
                "status": sr.checker_status,
                "captured": sr.captured,
                "stolen": sr.stolen,
                "recent": [sr.checker_status] + list(sr.previous_checker_status),
            }
        return d

    def first_bloods_json(board: Any) -> Dict[str, Any]:
        return {
            s.name: [
                {"name": b.name, "ts": b.ts, "confirmed": b.confirmed, "level": b.level}
                for b in s.first_blood
            ]
            for s in board.services
            if s.first_blood
        }

    if multi:
        jsonable: Any = {
            "rounds": [
                {
                    "round": board.round,
                    "ranking": [row_dict(board, r) for r in rows],
                    "first_bloods": first_bloods_json(board),
                }
                for board, rows in per_round
            ]
        }
    else:
        board, rows = per_round[0]
        jsonable = {
            "round": board.round,
            "ranking": [row_dict(board, r) for r in rows],
            "first_bloods": first_bloods_json(board),
        }

    def render() -> None:
        cols: List[Tuple[str, bool]] = []  # (header, right-align)
        if multi:
            cols.append(("ROUND", True))
        cols += [
            ("RANK", True),
            ("TEAM", False),
            ("POINTS", True),
            ("DELTA", True),
            ("OFF", True),
            ("DEF", True),
            ("SLA", True),
        ]
        if service is not None:
            cols += [("STATUS", False), ("CAP", True), ("STL", True), ("RECENT", False)]
        headers = [h for h, _ in cols]
        right = tuple(i for i, (_, r) in enumerate(cols) if r)

        table_rows: List[List[Any]] = []
        for board, rows in per_round:
            for r in rows:
                row: List[Any] = []
                if multi:
                    row.append(board.round)
                name = names.get(r.team_id, str(r.team_id))

                def comp(value: float, delta: float) -> str:
                    return f"{value:.1f} (" + _delta_colored(out, delta) + ")"

                row += [
                    r.rank,
                    name,
                    f"{r.points:.1f}",
                    _delta_colored(out, r.delta_off + r.delta_def + r.delta_sla),
                    comp(r.off_points, r.delta_off),
                    comp(r.def_points, r.delta_def),
                    comp(r.sla_points, r.delta_sla),
                ]
                sr = svc_result(board, r)
                if service is not None:
                    if sr is not None:
                        recent = " ".join(
                            _status_glyph(out, s)
                            for s in (
                                [sr.checker_status] + list(sr.previous_checker_status)
                            )[:5]
                        )
                        row += [
                            _status_color(out, sr.checker_status),
                            sr.captured,
                            sr.stolen,
                            recent,
                        ]
                    else:
                        row += ["-", "-", "-", "-"]
                table_rows.append(row)

        if multi:
            caption = "scoreboard @ rounds " + ", ".join(
                str(b.round) for b, _ in per_round
            )
        else:
            caption = f"scoreboard @ round {per_round[0][0].round}"
        if service is not None:
            caption += f"  ({service})"
        print(out.c(caption, "dim"))
        out.table(headers, table_rows, right=right)

        # first bloods per round (only services that have one)
        for board, _ in per_round:
            fbs = [s for s in board.services if s.first_blood]
            if not fbs:
                continue
            label = f"first bloods @ round {board.round}:" if multi else "first bloods:"
            print(out.c(label, "dim"))
            for s in fbs:
                # the feed does NOT say which flag store each entry belongs to (the emitter
                # dedups by payload, then drops it). We mirror the API's ordering into one slot
                # per store and mark the still-unblooded stores as TBD.
                parts = [
                    out.c(b.name + ("" if b.confirmed else " (unconfirmed)"), "yellow")
                    for b in s.first_blood
                ]
                parts += [
                    out.c("TBD", "dim") for _ in range(s.flag_stores - len(parts))
                ]
                print(f"  {s.name}\t{', '.join(parts)}")

    _emit(json_out, jsonable, render)
    return 0


def _team_history(
    api: EcscApiSync, out: Out, json_out: bool, team: str, round_: Optional[int]
) -> int:
    meta = _scoreboard_teams(api)
    names = {tid: t.name for tid, t in meta.items()}
    try:
        team_id = _resolve_team_id(team, meta)
    except AmbiguousTeam as e:
        print(out.c(str(e), "red"), file=sys.stderr)
        return 3
    if team_id is None:
        print(out.c(f"could not resolve team: {team}", "red"), file=sys.stderr)
        return 3
    hist = api.team_history(team_id)
    totals = hist.total_per_round()
    base = {"team_id": team_id, "name": names.get(team_id), "services": hist.services}
    # default: whole history; -r/--round focuses a single round
    if round_ is None:
        rounds = list(range(len(totals)))
        jsonable: Dict[str, Any] = {
            **base,
            "points": hist.points,
            "total_per_round": totals,
        }
    else:
        which = _resolve_round(round_, len(totals))
        rounds = [which] if 0 <= which < len(totals) else []
        jsonable = {
            **base,
            "round": which if rounds else None,
            "points": [
                hist.points[s][which]
                if rounds and which < len(hist.points[s])
                else None
                for s in range(len(hist.services))
            ],
            "total": totals[which] if rounds else None,
        }

    def render() -> None:
        headers = ["ROUND"] + hist.services + ["TOTAL"]
        table_rows: List[List[Any]] = []
        for t in rounds:
            row: List[Any] = [t]
            for s in range(len(hist.services)):
                row.append(
                    f"{hist.points[s][t]:.1f}" if t < len(hist.points[s]) else "-"
                )
            row.append(f"{totals[t]:.1f}")
            table_rows.append(row)
        print(out.c(f"{names.get(team_id, team_id)} (team {team_id})", "dim"))
        out.table(headers, table_rows, right=tuple(range(len(headers))))

    _emit(json_out, jsonable, render)
    return 0


# ---------------------------------------------------------------------------
# Click wiring
# ---------------------------------------------------------------------------


def common_options(f: Callable) -> Callable:
    """Attach the options shared by every command (json, url, cache, timing, colour)."""
    options = [
        click.option("-j", "--json", "json_out", is_flag=True, help="output JSON"),
        click.option(
            "-u",
            "--url",
            default="",
            metavar="URL",
            help=f"game base URL, api/ directory or attack.json URL (default: selected host or ${ENV_VAR})",
        ),
        click.option(
            "-H",
            "--host",
            default="",
            metavar="NAME|URL",
            help="saved host name (never dotted), or a raw host/api URL used like --url "
            "(default: selected host)",
        ),
        click.option(
            "--cache-dir", default=None, metavar="DIR", help="cache directory"
        ),
        click.option(
            "--lifetime",
            type=float,
            default=None,
            metavar="SEC",
            help="cache lifetime in seconds (default: 30 or selected host)",
        ),
        click.option(
            "--timeout",
            type=float,
            default=None,
            metavar="SEC",
            help="request timeout in seconds (default: 10 or selected host)",
        ),
        click.option(
            "--no-color", "no_color", is_flag=True, help="disable coloured output"
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _group_host() -> str:
    """The ``-H/--host`` given before the subcommand, if any (stored on the group's context)."""
    ctx = click.get_current_context(silent=True)
    obj = ctx.obj if ctx is not None else None
    return str(obj.get("host", "")) if isinstance(obj, dict) else ""


class UnknownHost(Exception):
    """A ``--host`` argument shaped like a saved name (no URL punctuation) that isn't one."""


def _resolve_connection(common: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge explicit ``--url``/``--host``/timing options over the selected host's properties over
    built-in defaults. ``--host`` picks a saved host by name, or - if it is URL-shaped - is used
    directly as a raw URL, same as ``--url``; it may be given before or after the subcommand, and
    the subcommand's own value wins. An empty URL is left empty so the client can still fall back
    to ``$ECSC_API``.

    An explicit ``--url`` settles the connection by itself, so ``--host`` is not resolved at all
    when one is given - a stale name left in a shell alias must not reject a command that said
    outright where to go.

    Otherwise an unknown argument that could still be a name raises :class:`UnknownHost` rather
    than being requested as a URL: names carry no URL punctuation, so a typo cannot be mistaken
    for a host.

    The built-in default host is the last resort, after ``$ECSC_API``: it exists so the CLI works
    with nothing configured at all, and an environment that names a game is something configured.
    A host the player actually selected still wins over the environment.
    """
    store = HostStore.load()
    host_arg = "" if common["url"] else (common["host"] or _group_host())
    host: Optional[Host]
    if host_arg:
        host = store.hosts.get(host_arg)
        if host is None:
            if valid_host_name(host_arg):
                raise UnknownHost(
                    f"unknown host: {host_arg}; known hosts: "
                    + ", ".join(sorted(store.hosts))
                )
            host = Host(name="", url=host_arg)
    elif store.selected is None and ENV_VAR in os.environ:
        host = None
    else:
        host = store.selected_host()
    url = common["url"] or (host.url if host else "")
    lifetime = common["lifetime"]
    if lifetime is None:
        lifetime = host.lifetime if host and host.lifetime is not None else 30.0
    timeout = common["timeout"]
    if timeout is None:
        timeout = host.timeout if host and host.timeout is not None else 10.0
    cache_dir = common["cache_dir"] or (host.cache_dir if host else None)
    return {
        "url": url,
        "lifetime": lifetime,
        "timeout": timeout,
        "cache_dir": cache_dir,
    }


def _execute(
    common: Dict[str, Any], work: Callable[[EcscApiSync, Out, bool], int]
) -> int:
    """Build the client + output helper from the shared options and selected host, then run ``work``."""
    json_out = common["json_out"]
    color = not common["no_color"] and not json_out and sys.stdout.isatty()
    out = Out(color)

    try:
        conn = _resolve_connection(common)
    except UnknownHost as e:
        print(out.c(str(e), "red"), file=sys.stderr)
        return 3
    if not conn["url"] and not os.environ.get(ENV_VAR):
        print(
            out.c(
                "please select a host or configure a URL: run "
                "'ecsc2026ad host add <name> <url>' and 'ecsc2026ad host select <name>', "
                f"pass --url/--host, or set ${ENV_VAR}",
                "red",
            ),
            file=sys.stderr,
        )
        return 2

    kwargs: Dict[str, Any] = {"lifetime": conn["lifetime"], "timeout": conn["timeout"]}
    if conn["cache_dir"]:
        kwargs["tmp_directory"] = conn["cache_dir"]
    if sys.stderr.isatty():
        kwargs["progress"] = RequestSpinner
    try:
        api = EcscApiSync(conn["url"], **kwargs)
    except Exception as e:  # noqa - configuration error
        print(out.c(str(e), "red"), file=sys.stderr)
        return 2

    try:
        return int(work(api, out, json_out))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa - network / parse error
        print(out.c(f"error: {e}", "red"), file=sys.stderr)
        return 1


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(
    _package_version(),
    "--version",
    prog_name="ecsc2026ad",
    message="%(prog)s %(version)s",
)
@click.option(
    "-H",
    "--host",
    default="",
    metavar="NAME|URL",
    help="saved host name (never dotted), or a raw host/api URL, used by every subcommand "
    "(a subcommand's own -H/--host wins)",
)
@click.pass_context
def cli(ctx: click.Context, host: str) -> None:
    """Fast, cached access to the ECSC 2026 attack-info and scoreboard APIs."""
    ctx.obj = {"host": host}
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(1)


@cli.command(
    "status",
    context_settings=CONTEXT_SETTINGS,
    help="current game state + attack-info summary",
)
@common_options
def status_cmd(**common: Any) -> int:
    return _execute(common, _status)


@cli.command("teams", context_settings=CONTEXT_SETTINGS, help="list attackable teams")
@common_options
@click.option("--grep", metavar="TEXT", help="filter by name/affiliation/ip substring")
def teams_cmd(grep: Optional[str], **common: Any) -> int:
    return _execute(common, lambda api, out, j: _teams(api, out, j, grep))


_ROUND_OPTION = click.option(
    "-r",
    "--round",
    "round_",
    type=int,
    metavar="N",
    help="round (negative counts back from the latest, -1 = last; default: latest)",
)


@cli.command(
    "services",
    context_settings=CONTEXT_SETTINGS,
    help="list services with attacker/victim counts",
)
@common_options
@_ROUND_OPTION
def services_cmd(round_: Optional[int], **common: Any) -> int:
    return _execute(common, lambda api, out, j: _services(api, out, j, round_))


@cli.command(
    "attack-info",
    context_settings=CONTEXT_SETTINGS,
    help="get flag IDs for a service (and optionally a team)",
)
@common_options
@click.option("--raw", is_flag=True, help="keep the nested round/store structure")
@click.option(
    "-r",
    "--round",
    "round_",
    type=int,
    metavar="N",
    help="only this round's flag IDs (negative counts back, -1 = last; default: all rounds)",
)
@click.argument("service")
@click.argument("team", required=False)
def flag_ids_cmd(
    service: str,
    team: Optional[str],
    raw: bool,
    round_: Optional[int],
    **common: Any,
) -> int:
    return _execute(
        common,
        lambda api, out, j: _flag_ids(api, out, j, service, team, raw, round_),
    )


@cli.command(
    "scoreboard", context_settings=CONTEXT_SETTINGS, help="show the scoreboard ranking"
)
@common_options
@click.option(
    "-r",
    "--round",
    "rounds",
    type=int,
    metavar="N",
    multiple=True,
    help="round to show (repeatable; negative counts back from the latest, "
    "-1 = last; default: latest published)",
)
@click.option("-n", "--top", type=int, metavar="N", help="only the top N teams")
@click.option(
    "-t",
    "--team",
    "teams",
    metavar="ID|NAME",
    multiple=True,
    help="only this team (repeatable); a single team with no --round shows its history",
)
@click.option(
    "-s",
    "--service",
    metavar="NAME",
    help="add this service's status/cap/steal columns",
)
@click.argument("round_", required=False, type=int, metavar="ROUND")
def scoreboard_cmd(
    round_: Optional[int],
    rounds: Tuple[int, ...],
    top: Optional[int],
    teams: Tuple[str, ...],
    service: Optional[str],
    **common: Any,
) -> int:
    return _execute(
        common,
        lambda api, out, j: _scoreboard(
            api, out, j, round_, rounds, top, teams, service
        ),
    )


@cli.command(
    "team",
    context_settings=CONTEXT_SETTINGS,
    help="a team's per-service points over time",
)
@common_options
@_ROUND_OPTION
@click.argument("team")
def team_history_cmd(team: str, round_: Optional[int], **common: Any) -> int:
    return _execute(
        common, lambda api, out, j: _team_history(api, out, j, team, round_)
    )


@cli.group("host", context_settings=CONTEXT_SETTINGS)
def host_group() -> None:
    """Manage saved game hosts. The selected host's URL is used when --url is omitted, falling back
    to the built-in "default" host (the ECSC 2026 production scoreboard) when nothing is selected."""


@host_group.command(
    "add",
    context_settings=CONTEXT_SETTINGS,
    help="save a host (creating or updating it)",
)
@click.argument("name")
@click.argument("url")
@click.option(
    "--lifetime",
    type=float,
    default=None,
    metavar="SEC",
    help="cache lifetime override",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    metavar="SEC",
    help="request timeout override",
)
@click.option(
    "--cache-dir", default=None, metavar="DIR", help="cache directory override"
)
@click.option(
    "--select/--no-select",
    "select",
    default=None,
    help="select this host after adding (default: select if none is selected yet)",
)
def host_add_cmd(
    name: str,
    url: str,
    lifetime: Optional[float],
    timeout: Optional[float],
    cache_dir: Optional[str],
    select: Optional[bool],
) -> int:
    if not valid_host_name(name):
        print(
            f"invalid host name: {name}\n"
            "host names may not contain '.', ':', '/' or whitespace, so that a --host argument "
            "is either a name or a URL",
            file=sys.stderr,
        )
        return 2
    url = _assume_scheme(url)
    store = HostStore.load()
    existed = name in store.hosts
    store.hosts[name] = Host(
        name=name, url=url, lifetime=lifetime, timeout=timeout, cache_dir=cache_dir
    )
    if select or (select is None and store.selected is None):
        store.selected = name
    store.save()
    verb = "updated" if existed else "added"
    marker = " (selected)" if store.selected == name else ""
    print(f"{verb} host {name!r} -> {url}{marker}")
    return 0


@host_group.command("list", context_settings=CONTEXT_SETTINGS, help="list saved hosts")
@click.option("-j", "--json", "json_out", is_flag=True, help="output JSON")
@click.option("--no-color", "no_color", is_flag=True, help="disable coloured output")
def host_list_cmd(json_out: bool, no_color: bool) -> int:
    store = HostStore.load()
    out = Out(not no_color and not json_out and sys.stdout.isatty())
    hosts = sorted(store.hosts.values(), key=lambda h: h.name)
    selected = store.selected_host()
    selected_name = selected.name if selected else None
    if json_out:
        print(
            json.dumps(
                {
                    "selected": selected_name,
                    "hosts": [
                        {
                            "name": h.name,
                            "url": h.url,
                            "lifetime": h.lifetime,
                            "timeout": h.timeout,
                            "cache_dir": h.cache_dir,
                        }
                        for h in hosts
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    rows = [
        [
            out.c("*", "green") if selected_name == h.name else "",
            h.name,
            h.url,
            "" if h.lifetime is None else h.lifetime,
            "" if h.timeout is None else h.timeout,
        ]
        for h in hosts
    ]
    out.table(["", "NAME", "URL", "LIFETIME", "TIMEOUT"], rows)
    return 0


@host_group.command("rm", context_settings=CONTEXT_SETTINGS, help="remove a saved host")
@click.argument("name")
def host_rm_cmd(name: str) -> int:
    store = HostStore.load()
    if name not in store.hosts:
        print(f"unknown host: {name}", file=sys.stderr)
        return 3
    del store.hosts[name]
    if store.selected == name:
        store.selected = None
    store.save()
    print(f"removed host {name!r}")
    return 0


@host_group.command(
    "select",
    context_settings=CONTEXT_SETTINGS,
    help="select the host used when --url is omitted; omit NAME to deselect",
)
@click.argument("name", required=False)
def host_select_cmd(name: Optional[str]) -> int:
    store = HostStore.load()
    if name is None:
        store.selected = None
        store.save()
        fallback = store.selected_host()
        print(
            "deselected host"
            + (
                f", falling back to {fallback.name!r} -> {fallback.url}"
                if fallback
                else ""
            )
        )
        return 0
    if name not in store.hosts:
        print(f"unknown host: {name}", file=sys.stderr)
        if store.hosts:
            print("known hosts: " + ", ".join(sorted(store.hosts)), file=sys.stderr)
        return 3
    store.selected = name
    store.save()
    print(f"selected host {name!r} -> {store.hosts[name].url}")
    return 0


def run(argv: Optional[Sequence[str]] = None) -> int:
    """Invoke the CLI and return an exit code (used by :func:`main` and the tests)."""
    args = list(argv) if argv is not None else None
    try:
        rv = cli.main(args=args, prog_name="ecsc2026ad", standalone_mode=False)
        return int(rv) if rv is not None else 0
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except SystemExit as e:  # safety net for eager callbacks that call sys.exit
        return int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
