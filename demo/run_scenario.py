"""Run a demo scenario: python demo/run_scenario.py s1

# Owner: Team B
Loads demo/scenarios/<name>.yaml, calls sim_load_scenario, then replays the steps via sim_set_state.
Never calls Altice write tools.
"""
from __future__ import annotations

import sys


def main(name: str) -> None:
    raise NotImplementedError(f"Team B: run scenario {name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1")
