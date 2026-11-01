"""
Refresh scope/rijksoverheid.txt from the Register Internetdomeinen Overheid.

The register (https://organisaties.overheid.nl/domeinen) is the government's own
record of the domains it holds, so its domains go into the scope directly -- they
are NOT run through verify_rijksoverheid.py.

Steps:
  1. Fetch every top-level domain in the register (engine/overheid_domeinen.py)
  2. Add them to scope/rijksoverheid.txt
  3. Remove them from scope/rijksoverheid_invalid.txt, where earlier verifier runs
     may have put them

Domains are only ever added here, never removed: a domain that drops out of the
register stays in scope until revalidate_scope.py verifies it like any other.

Usage:
    python engine/refresh_rijksoverheid.py
    python engine/refresh_rijksoverheid.py --dry-run
"""

import argparse
import sys
from pathlib import Path

from overheid_domeinen import fetch_domains

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCOPE_DIR    = Path(__file__).parent.parent / "scope"
VALID_FILE   = SCOPE_DIR / "rijksoverheid.txt"
INVALID_FILE = SCOPE_DIR / "rijksoverheid_invalid.txt"


def read_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def write_sorted(path: Path, domains: set[str]) -> None:
    # newline="\n" keeps the files LF-terminated on Windows too.
    path.write_text("\n".join(sorted(domains)) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(
        description="Add the Register Internetdomeinen Overheid domains to the scope")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing the scope files")
    args = parser.parse_args()

    registered = fetch_domains()

    existing_valid   = read_file(VALID_FILE)
    existing_invalid = read_file(INVALID_FILE)

    added    = registered - existing_valid - existing_invalid
    promoted = registered & existing_invalid
    print(f"\nExisting confirmed : {len(existing_valid)}")
    print(f"Existing invalid   : {len(existing_invalid)}")
    print(f"In the register    : {len(registered)}")
    print(f"New                : {len(added)}")
    print(f"Promoted (invalid) : {len(promoted)}")

    if not added and not promoted:
        print("\nScope is up to date.")
        return
    if args.dry_run:
        print("\nDRY RUN - scope files left untouched.")
        return

    new_valid   = existing_valid | registered
    new_invalid = existing_invalid - registered
    write_sorted(VALID_FILE, new_valid)
    write_sorted(INVALID_FILE, new_invalid)
    print(f"\nscope/rijksoverheid.txt         : {len(new_valid)} domains "
          f"(+{len(new_valid) - len(existing_valid)})")
    print(f"scope/rijksoverheid_invalid.txt : {len(new_invalid)} domains "
          f"({len(new_invalid) - len(existing_invalid):+d})")
    print("\nDone.")


if __name__ == "__main__":
    main()
