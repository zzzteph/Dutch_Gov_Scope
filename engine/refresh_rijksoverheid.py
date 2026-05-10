"""
Full refresh pipeline for scope/rijksoverheid.txt.

Steps:
  1. Fetch fresh domains from communicatierijk.nl
  2. Find domains not already in the confirmed scope (new/unknown)
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
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import tldextract
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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


def get_root_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}"


def fetch_communicatierijk() -> set[str]:
    """Fetch the official government website register from communicatierijk.nl."""
    print("Fetching communicatierijk.nl websiteregister …")
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas not installed (pip install pandas odfpy)")
        return set()

    base_url = "https://www.communicatierijk.nl"
    page_url = base_url + "/documenten/2016/05/26/websiteregister"

    resp = requests.get(page_url, verify=False, timeout=15)
    resp.raise_for_status()

    match = re.search(r'href="([^"]+\.ods)"', resp.text)
    if not match:
        raise RuntimeError("ODS link not found on communicatierijk.nl page")

    ods_url = match.group(1)
    if not ods_url.startswith("http"):
        ods_url = base_url + ods_url

    tmp = Path(tempfile.gettempdir())
    ods_path = tmp / "communicatierijk.ods"
    csv_path = tmp / "communicatierijk.csv"

    ods_data = requests.get(ods_url, verify=False, timeout=30)
    ods_data.raise_for_status()
    ods_path.write_bytes(ods_data.content)

    df = pd.read_excel(ods_path, engine="odf")
    df.to_csv(csv_path, index=False)
    df_csv = pd.read_csv(csv_path)

    entries = set()
    if not df_csv.empty:
        col = df_csv.columns[0]
        for value in df_csv[col]:
            if isinstance(value, str):
                parsed = urlparse(value)
                domain = parsed.hostname or value.strip()
                if not domain:
                    continue
                root = get_root_domain(domain)
                # Skip header artifacts and invalid entries (must have a real TLD)
                ext = tldextract.extract(root)
                if ext.domain and ext.suffix and len(ext.suffix) >= 2:
                    entries.add(root)

    print(f"  → {len(entries)} domains from communicatierijk.nl")
    return entries


def read_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def write_sorted(path: Path, domains: set[str]) -> None:
    path.write_text("\n".join(sorted(domains)) + "\n", encoding="utf-8")


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

    # ── Step 1: fetch new domains ──────────────────────────────────────────
    fresh = fetch_communicatierijk()

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
