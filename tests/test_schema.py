"""
Checks on ``openapi.yaml`` itself, rather than on the code generated from it.

``openapi.yaml`` is a verbatim copy of the gameserver's own scoreboard schema. Copying it is
what makes these checks worth anything: validating the fixtures against a spec maintained in
this repo would only prove that both were edited together, which is exactly how five separate
drifts (round naming, the dropped online flag, affiliation moving to scoreboard_teams.json,
and the checker status vocabulary) went unnoticed.
"""

import json
import os
import unittest
from pathlib import Path
from typing import Any, ClassVar, Dict

import jsonschema
import yaml

_REPO = Path(__file__).parent.parent
_SPEC = _REPO / "openapi.yaml"
_RES = Path(__file__).parent / "res"

# fixture -> the schema the gameserver serves it under
_FIXTURES = {
    "scoreboard_current.json": "ScoreboardCurrent",
    "scoreboard_round_5.json": "ScoreboardRound",
    "scoreboard_round_6.json": "ScoreboardRound",
    "scoreboard_teams.json": "ScoreboardTeams",
    "scoreboard_team_2.json": "ScoreboardTeamHistory",
    "scoreboard_service_stats.json": "ScoreboardServiceStats",
    # decoded by ctf-attackapi rather than by us, but still our fixture to keep honest
    "attack.json": "AttackInfo",
}

_GAMESERVER = os.environ.get("ECSC_GAMESERVER")
_GAMESERVER_SPEC = (
    Path(_GAMESERVER).expanduser() / "scoreboard" / "schema" / "openapi.yaml"
    if _GAMESERVER
    else None
)


def _denullable(node: Any) -> Any:
    """
    Rewrite OpenAPI 3.0's ``nullable: true`` into a JSON Schema null union.

    jsonschema validates plain JSON Schema, where ``nullable`` is an unknown keyword that is
    ignored -- so without this every nullable field rejects the null the gameserver actually
    sends, and the fixtures look broken when they are correct.
    """
    if isinstance(node, dict):
        node = {key: _denullable(value) for key, value in node.items()}
        if node.pop("nullable", False) and "type" in node:
            node["type"] = [node["type"], "null"]
        return node
    if isinstance(node, list):
        return [_denullable(value) for value in node]
    return node


class SchemaTestCase(unittest.TestCase):
    _spec: ClassVar[Dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls._spec = _denullable(yaml.safe_load(_SPEC.read_text()))

    def test_every_fixture_matches_the_schema(self) -> None:
        for filename, schema_name in _FIXTURES.items():
            with self.subTest(fixture=filename):
                schema = dict(self._spec["components"]["schemas"][schema_name])
                # carry the component table along so internal $refs still resolve
                schema["components"] = self._spec["components"]
                data = json.loads((_RES / filename).read_bytes())
                jsonschema.validate(data, schema)

    def test_fixtures_cover_every_documented_response(self) -> None:
        documented = {
            content["schema"]["$ref"].rsplit("/", 1)[-1]
            for path in self._spec["paths"].values()
            for method in path.values()
            for response in method["responses"].values()
            for content in response["content"].values()
        }
        self.assertEqual(documented, set(_FIXTURES.values()))

    @unittest.skipUnless(
        _GAMESERVER_SPEC is not None and _GAMESERVER_SPEC.is_file(),
        "set ECSC_GAMESERVER=<path to gameserver checkout> to check the schema is current",
    )
    def test_schema_is_a_current_copy_of_the_gameservers(self) -> None:
        assert _GAMESERVER_SPEC is not None
        self.assertEqual(
            _GAMESERVER_SPEC.read_text(),
            _SPEC.read_text(),
            "openapi.yaml is stale; re-copy it from the gameserver and run"
            " ./schema-to-pydantic.sh",
        )
