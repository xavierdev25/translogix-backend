"""Regenerate (or check) every generated file under ``contracts/``.

    uv run python -m app.contracts            # regenerate openapi.yaml, schemas/, mocks/
    uv run python -m app.contracts --check    # fail if anything is stale (CI)

TypeScript types come from the YAML and need Node: ``contracts/types/generate.sh``.
"""

import argparse
import sys

from app.contracts.artifacts import CONTRACTS_DIR, render_artifacts, stale_artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.contracts")
    parser.add_argument("--check", action="store_true", help="only verify artifacts are current")
    args = parser.parse_args(argv)

    if args.check:
        problems = stale_artifacts()
        for problem in problems:
            print(f"stale: {problem}", file=sys.stderr)
        if problems:
            print("Regenerate: uv run python -m app.contracts && contracts/types/generate.sh")
            return 1
        print("contracts/ is up to date.")
        return 0

    for path, content in render_artifacts().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(CONTRACTS_DIR.parent)}")
    print("Now regenerate the TypeScript types: contracts/types/generate.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
