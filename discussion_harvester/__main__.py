from __future__ import annotations

import sys

from github_code_harvester.harvester import public_article_main, stackoverflow_dump_main, stackoverflow_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "stackoverflow":
        return stackoverflow_main(args[1:])
    if args and args[0] == "stackoverflow-dump":
        return stackoverflow_dump_main(args[1:])
    if args and args[0] in {"csdn", "zhihu"}:
        return public_article_main(args[0], args[1:])
    if not args:
        return stackoverflow_main([])
    print("Usage: python3 -m discussion_harvester {stackoverflow,stackoverflow-dump,csdn,zhihu} [options]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
