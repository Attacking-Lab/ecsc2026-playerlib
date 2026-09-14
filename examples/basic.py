"""Synchronous exploit example: fire at every team's ServiceA.

Usage: basic.py [API_URL]   (falls back to the ECSC_API environment variable)
"""

import sys

from ecsc2026ad import EcscApiSync


def pwn(ip: str, flag_id: str) -> None:
    print(f"attacking {ip} with flag id {flag_id}")


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    with EcscApiSync(url) as ecsc:
        info = ecsc.attack_info()
        for team in info.teams:
            for flag_id in info.flag_ids("ServiceA", team):
                pwn(team.ip, flag_id)


if __name__ == "__main__":
    main()
