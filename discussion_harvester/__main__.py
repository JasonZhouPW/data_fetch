from __future__ import annotations

import sys

from github_code_harvester.harvester import stackoverflow_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "stackoverflow":
        return stackoverflow_main(args[1:])
    if not args:
        return stackoverflow_main([])
    print("Usage: python3 -m discussion_harvester stackoverflow [options]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
