from __future__ import annotations

import argparse
import re
import sys


SERVICE_BASE_PATTERNS = (
    re.compile(r"^v\d+\.\d+\.\d+$"),
    re.compile(r"^release-\d{8}-\d{3}$"),
    re.compile(r"^service-base-\d{8}-\d{3}$"),
)

PROVIDER_PATTERNS = (
    re.compile(r"^provider-(python|go|javascript|rust)-\d{8}-\d{3}$"),
    re.compile(r"^providers-\d{8}-\d{3}$"),
)


def validate_service_base(tag: str) -> bool:
    return any(pattern.match(tag) for pattern in SERVICE_BASE_PATTERNS)


def validate_provider(tag: str) -> bool:
    return any(pattern.match(tag) for pattern in PROVIDER_PATTERNS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate EasyProtocol release tags.")
    parser.add_argument("--mode", required=True, choices=["service-base", "provider"])
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    tag = str(args.tag or "").strip()
    if not tag:
        raise SystemExit("release tag must not be empty")

    if args.mode == "service-base" and validate_service_base(tag):
        print(f"tag accepted: {tag}")
        return
    if args.mode == "provider" and validate_provider(tag):
        print(f"tag accepted: {tag}")
        return

    print(f"tag rejected for mode {args.mode}: {tag}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
