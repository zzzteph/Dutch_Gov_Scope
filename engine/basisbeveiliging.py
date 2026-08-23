"""
Harvest candidate Dutch government top-level domains from basisbeveiliging.nl.

This script only COLLECTS candidates. It deliberately does not decide whether a
domain belongs to the Dutch government -- that decision is made by the three-tier
pipeline in verify_rijksoverheid.py (raw HTTP + SSL -> rendered DOM -> Claude
vision), driven by revalidate_scope.py.

Every export row is collapsed to its registrable root domain, so the candidate
list only ever contains top-level domains -- never subdomains.

Usage:
    python engine/basisbeveiliging.py bb_candidates.txt
    python engine/basisbeveiliging.py bb_candidates.txt --exclude-known
    python engine/basisbeveiliging.py bb_candidates.txt --append --concurrency 12
    python engine/basisbeveiliging.py bb_candidates.txt --include-all-government

Then verify what came out, never trust it directly:
    python engine/revalidate_scope.py --source bb_candidates.txt
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import requests
import tldextract
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT         = Path(__file__).parent.parent
VALID_FILE   = ROOT / "scope" / "rijksoverheid.txt"
INVALID_FILE = ROOT / "scope" / "rijksoverheid_invalid.txt"

EXPORT_URL = "https://basisbeveiliging.nl/data/export/urls_only/NL/{category}/csv/"

# The umbrella category: every Dutch public-sector site, including municipalities,
# water boards and schools (~14.6k root domains). Far wider than the Rijksoverheid
# scope this repo tracks, so it is opt-in via --include-all-government.
UMBRELLA_CATEGORY = "government"

# Delay between retries of a failed export, in seconds.
BACKOFF_SECONDS = (5, 15, 30)

# One entry per category -- the previous list repeated most of these twice.
CATEGORIES = (
    "central_government_employment",
    "central_government_defense",
    "central_government_general_affairs",
    "central_government_agriculture",
    "central_government_health",
    "central_government_infrastructure",
    "central_government_finance",
    "central_government_economy",
    "central_government_interior_relations",
    "central_government_justice",
    "central_government_foreign_affairs",
    "central_government_education",
)


def get_root_domain(url: str) -> str | None:
    ext = tldextract.extract(url)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return None


def fetch_category(session: requests.Session, category: str, timeout: int,
                   attempts: int = 4) -> set[str]:
    """Download one category export and return the root domains it lists."""
    url = EXPORT_URL.format(category=category)
    domains: set[str] = set()

    response = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as exc:
            # The larger exports intermittently 500 while the server regenerates
            # them. Which categories fail varies run to run, so back off and retry
            # rather than silently dropping a whole category from the harvest.
            response = None
            if attempt == attempts:
                print(f"  [FAIL] {category}: {exc}", flush=True)
                return domains
            delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            print(f"  [RETRY] {category}: {exc} - retrying in {delay}s "
                  f"({attempt}/{attempts - 1})", flush=True)
            time.sleep(delay)

    for row in csv.reader(StringIO(response.text)):
        if len(row) > 1:
            # tldextract collapses any host to its registrable root, so a
            # subdomain in the export never reaches the candidate list.
            root = get_root_domain(row[1].strip())
            if root and root.endswith(".nl"):
                domains.add(root)

    print(f"  [ OK ] {category}: {len(domains)} domains", flush=True)
    return domains


def fetch_candidates(concurrency: int = 8, timeout: int = 20,
                     include_all_government: bool = False) -> set[str]:
    """Fetch every category export concurrently and return the merged root-domain set."""
    categories = CATEGORIES + ((UMBRELLA_CATEGORY,) if include_all_government else ())
    print(f"Fetching {len(categories)} basisbeveiliging.nl exports ...")
    entries: set[str] = set()
    with requests.Session() as session:
        session.headers["User-Agent"] = "DutchGovScope/1.0 (+https://github.com/zzzteph/DutchGovScope)"
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for result in pool.map(lambda c: fetch_category(session, c, timeout), categories):
                entries |= result
    print(f"  -> {len(entries)} unique root domains")
    return entries


def read_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def main():
    parser = argparse.ArgumentParser(
        description="Harvest candidate Dutch government domains from basisbeveiliging.nl")
    parser.add_argument("output", help="File to write candidate domains to (one per line)")
    parser.add_argument("--exclude-known", action="store_true",
                        help="Drop domains already present in either scope file")
    parser.add_argument("--append", action="store_true",
                        help="Merge with the existing contents of the output file")
    parser.add_argument("--include-all-government", action="store_true",
                        help="Also pull the umbrella 'government' export (~14.6k domains: "
                             "municipalities, water boards, schools - not just Rijksoverheid)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    candidates = fetch_candidates(args.concurrency, args.timeout, args.include_all_government)

    if args.exclude_known:
        known = read_set(VALID_FILE) | read_set(INVALID_FILE)
        before = len(candidates)
        candidates -= known
        print(f"  -> {len(candidates)} new ({before - len(candidates)} already in the scope files)")

    out_path = Path(args.output)
    if args.append:
        candidates |= read_set(out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sorted(candidates)) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(candidates)} candidate domains to {out_path}")
    print("These are UNVERIFIED -- run them through verify_rijksoverheid.py "
          "(or revalidate_scope.py --source <file>) before adding them to the scope.")


if __name__ == "__main__":
    main()
