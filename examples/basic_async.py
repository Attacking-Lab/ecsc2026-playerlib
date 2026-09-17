"""Asynchronous exploit example.

Usage: basic_async.py [API_URL]   (falls back to $ECSC_API, then the ECSC 2026 scoreboard)
"""

import asyncio
import sys

from ecsc2026ad import EcscApiAsync


async def pwn(ip: str, flag_id: str) -> None:
    print(f"attacking {ip} with flag id {flag_id}")


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    async with EcscApiAsync(url) as ecsc:
        info = await ecsc.attack_info()
        await asyncio.gather(
            *(
                pwn(team.ip, flag_id)
                for team in info.teams
                for flag_id in info.flag_ids("ServiceA", team)
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
