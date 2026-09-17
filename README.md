ECSC 2026 Player Library
========================

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/license/mit)
![Python](https://img.shields.io/pypi/pyversions/ecsc2026ad?label=python)
![Types](https://img.shields.io/pypi/types/ecsc2026ad?label=types)
[![Python package tests](https://github.com/Attacking-Lab/ecsc2026-playerlib/actions/workflows/python-package.yml/badge.svg)](https://github.com/Attacking-Lab/ecsc2026-playerlib/actions/workflows/python-package.yml)
[![PyPI version](https://img.shields.io/pypi/v/ecsc2026ad?label=pypi)](https://pypi.org/project/ecsc2026ad)
![Downloads](https://img.shields.io/pypi/dm/ecsc2026ad?label=downloads)


Attack info and scoreboard for ECSC 2026, in your exploits and in your shell!

The gameserver publishes everything a player needs as static JSON under one `api/` directory: who to
attack and with which flag IDs, and how everyone is scoring. Exploits run every round against every
team, so they ask for that data constantly, and re-downloading it congests our network and slows
down your exploits.

This package fetches, decodes and caches all of it, as typed dataclasses rather than raw JSON.

Features
--------

- Two-tier caching, in memory and on disk, shared between threads, processes and containers
- Direct access from your exploits ([sync](./examples/basic.py) or [async](./examples/basic_async.py))
- The whole game API, not just attack info: [scoreboard](./examples/scoreboard.py), per-team point
  history, and per-service attacker/victim stats
- A CLI for the same data, with `-j/--json` on every command
- Fully typed, checked with mypy

Quick-Start
-----------

```shell
pip install ecsc2026ad
```

It already points at the game; pass another URL per client or set `ECSC_API` to override it:

```python
from ecsc2026ad import EcscApiSync

with EcscApiSync() as ecsc:  # default: https://scoreboard.ad.ecsc2026.de
    info = ecsc.attack_info()
    for team in info.teams:
        for flag_id in info.flag_ids("ServiceA", team):
            pwn(team.ip, flag_id)
```

The async client is the same API with `await`, and both are context managers:

```python
from ecsc2026ad import EcscApiAsync

async with EcscApiAsync() as ecsc:  # default: https://scoreboard.ad.ecsc2026.de
    info = await ecsc.attack_info()
    board = await ecsc.scoreboard()  # latest published round
    print(board.top(5))
```

Every endpoint is cached, so calling `attack_info()` in a loop over teams costs one request per
round, not one per call. Entering either client as a context manager additionally pools the
connection for the length of the block, so a burst -- attack info plus a dozen scoreboard files --
handshakes once instead of a dozen times. Outside a block every call stands on its own, which is
what keeps a fully cached run from importing aiohttp at all.

Command line
------------

```shell
ecsc2026ad status                       # game state and attack-info summary
ecsc2026ad teams                        # attackable teams
ecsc2026ad attack-info ServiceA         # flag IDs for every team
ecsc2026ad attack-info ServiceA nop     # ... or for one of them
ecsc2026ad scoreboard -s ServiceA       # ranking, per service
ecsc2026ad services                     # attacker/victim counts
ecsc2026ad team 2                       # one team's points over time
```

Save the game URL instead of passing it every time, or point a single command somewhere else with
`-H/--host`:

```shell
ecsc2026ad host add ecsc https://scoreboard.ad.ecsc2026.de
ecsc2026ad host select ecsc
ecsc2026ad -H 10.13.37.1:4200 status    # a raw address works too
```

A URL without a scheme gets `http://`, and a saved host name may not contain a period -- so a
dotted `-H` value is an address, not a name that failed to resolve.

The CLI shares the on-disk cache with the library, so a shell loop and a running exploit do not
re-request the same round.

Attack info
-----------

`attack.json` is fetched, cached and decoded by
[ctf-attackapi](https://github.com/Attacking-Lab/ctf-attackapi), which speaks the formats of several
attack-defense games; ECSC 2026 is its `atklab` dialect. `AttackInfo` and `Team` are that package's
types, re-exported here, so `info.team(...)`, `info.flag_ids(...)` and `info.flag_ids_raw(...)`
behave exactly as they do there, and an exploit written against one works against the other.

This package adds the scoreboard endpoints on top, which are outside that package's scope. Its
models are generated from the gameserver's own OpenAPI schema, vendored here as `openapi.yaml` and
regenerated with `./schema-to-pydantic.sh`.
