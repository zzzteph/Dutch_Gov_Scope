"""
Full refresh pipeline for scope/rijksoverheid.txt.

Steps:
  1. Fetch candidate .nl domains from the Register Internetdomeinen Overheid
     (organisaties.overheid.nl/domeinen, see overheid_domeinen.py)
  2. Find domains not already in either scope file (new/unknown)
  3. Run verify_rijksoverheid.py on new domains only
  4. Update scope/rijksoverheid.txt   — all confirmed (existing + newly verified)
  5. Update scope/rijksoverheid_invalid.txt — rejected + manual_review (append, no duplicates)

Usage:
    python engine/refresh_rijksoverheid.py
    python engine/refresh_rijksoverheid.py --no-vision   # skip browser + vision
    python engine/refresh_rijksoverheid.py --concurrency 15
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from overheid_domeinen import fetch_candidates

# Load .env if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCOPE_DIR  = Path(__file__).parent.parent / "scope"
VALID_FILE = SCOPE_DIR / "rijksoverheid.txt"
INVALID_FILE = SCOPE_DIR / "rijksoverheid_invalid.txt"
VERIFY_SCRIPT = Path(__file__).parent / "verify_rijksoverheid.py"


def read_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def write_sorted(path: Path, domains: set[str]) -> None:
    # newline="\n" keeps the files LF-terminated on Windows too.
    path.write_text("\n".join(sorted(domains)) + "\n", encoding="utf-8", newline="\n")


def run_verifier(domain_list: list[str], output_dir: Path, args) -> dict:
    """Write domains to a temp file and run verify_rijksoverheid.py on them."""
    tmp_input = output_dir / "_input.txt"
    tmp_input.write_text("\n".join(domain_list), encoding="utf-8")

    cmd = [
        sys.executable, str(VERIFY_SCRIPT),
        str(tmp_input),
        "--output-dir", str(output_dir),
        "--concurrency", str(args.concurrency),
    ]
    if args.no_vision:
        cmd.append("--no-vision")

    subprocess.run(cmd, check=True)

    return {
        "confirmed":     read_file(output_dir / "confirmed.txt"),
        "rejected":      read_file(output_dir / "rejected.txt"),
        "manual_review": read_file(output_dir / "manual_review.txt"),
    }


def main():
    parser = argparse.ArgumentParser(description="Refresh rijksoverheid scope")
    parser.add_argument("--no-vision", action="store_true",
                        help="Skip Tier 1.5 + Tier 2 (faster, no API cost)")
    parser.add_argument("--concurrency", type=int, default=15)
    args = parser.parse_args()

    # ── Step 1: fetch candidates from the register ────────────────────────
    fresh = fetch_candidates()

    # ── Step 2: find what's new ────────────────────────────────────────────
    existing_valid   = read_file(VALID_FILE)
    existing_invalid = read_file(INVALID_FILE)
    already_known    = existing_valid | existing_invalid

    new_domains = sorted(fresh - already_known)
    print(f"\nExisting confirmed : {len(existing_valid)}")
    print(f"Existing invalid   : {len(existing_invalid)}")
    print(f"Fresh from register: {len(fresh)}")
    print(f"New / unknown      : {len(new_domains)}")

    if not new_domains:
        print("\nNo new domains to verify. Scope is up to date.")
        return

    # ── Step 3: verify new domains ─────────────────────────────────────────
    work_dir = Path(tempfile.gettempdir()) / "rijksoverheid_verify"
    work_dir.mkdir(exist_ok=True)

    print(f"\nRunning verification on {len(new_domains)} new domains …")
    results = run_verifier(new_domains, work_dir, args)

    # ── Step 4: update scope/rijksoverheid.txt ─────────────────────────────
    new_confirmed = existing_valid | results["confirmed"]
    write_sorted(VALID_FILE, new_confirmed)
    print(f"\nscope/rijksoverheid.txt      : {len(new_confirmed)} domains"
          f"  (+{len(results['confirmed'])} new)")

    # ── Step 5: update scope/rijksoverheid_invalid.txt ────────────────────
    new_invalid = existing_invalid | results["rejected"] | results["manual_review"]
    write_sorted(INVALID_FILE, new_invalid)
    print(f"scope/rijksoverheid_invalid.txt: {len(new_invalid)} domains"
          f"  (+{len(results['rejected']) + len(results['manual_review'])} new)")

    print("\nDone.")


if __name__ == "__main__":
    main()
