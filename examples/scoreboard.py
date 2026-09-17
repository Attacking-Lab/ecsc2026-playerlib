"""Read the scoreboard: game state, ranking, and per-service stats.

Usage: scoreboard.py [API_URL]   (falls back to $ECSC_API, then the ECSC 2026 scoreboard)
"""

import sys

from ecsc2026ad import EcscApiSync


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    with EcscApiSync(url) as ecsc:
        state = ecsc.game_state()
        print(
            f"round {state.current_round}, state {state.state.name}, scoreboard at round {state.scoreboard_round}"
        )

        teams = ecsc.scoreboard_teams()
        board = ecsc.scoreboard()  # latest published round
        print(f"\nTop 5 after round {board.round}:")
        for row in board.top(5):
            name = teams[row.team_id].name if row.team_id in teams else str(row.team_id)
            print(
                f"  #{row.rank:2d} {name:20s} {row.points:8.1f}  (off {row.off_points} / def {row.def_points})"
            )

        # which teams look weakest on ServiceA right now?
        print("\nServiceA checker status:")
        for row in board.ranking:
            res = board.result(row.team_id, "ServiceA")
            if res is not None and res.checker_status != "SUCCESS":
                name = (
                    teams[row.team_id].name
                    if row.team_id in teams
                    else str(row.team_id)
                )
                print(f"  {name}: {res.checker_status}")


if __name__ == "__main__":
    main()
