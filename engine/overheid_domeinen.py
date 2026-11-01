"""
Fetch the top-level domains in the Register Internetdomeinen Overheid
(https://organisaties.overheid.nl/domeinen), the successor of the
communicatierijk.nl websiteregister.

The register is the government's own record of the domains it holds, so these
domains are taken as-is: unlike the basisbeveiliging.nl candidates they are NOT
run through verify_rijksoverheid.py.

The register is crawled the way a visitor would walk it: the index page lists the
categories (Ministeries, Agentschappen, Gemeenten, ...), and every category page
links two CSV exports that cover all of its organisations at once:

    Domeinnaamregistraties   domain names the organisation has registered
    Domeinen                 host names the organisation has in use

The exports hold the same rows as the per-organisation pages, so there is no need
to visit each organisation separately. Every row is collapsed to its registrable
root domain, so the output only ever contains top-level domains.

Two kinds of row are left out, because the register does not vouch for their root:

  * Domeinen rows for a host *below* a domain the organisation did not register,
    e.g. almere.notubiz.nl or kadaster.github.io/imbrt. Those are the
    organisation's tenancy on a vendor platform, and their root (notubiz.nl,
    github.io) belongs to the vendor. A Domeinen row only counts when it is the
    apex itself or its www host.
  * Rows whose registration or usage end date has passed: a lapsed domain may
    since have been registered by someone else. --include-ended keeps them.

Usage:
    python engine/overheid_domeinen.py od_domains.txt
    python engine/overheid_domeinen.py od_domains.txt --categories Ministeries,Politie
    python engine/overheid_domeinen.py od_domains.txt --exports registraties --exclude-known
    python engine/overheid_domeinen.py --list-categories
"""

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import requests
import tldextract

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT         = Path(__file__).parent.parent
VALID_FILE   = ROOT / "scope" / "rijksoverheid.txt"
INVALID_FILE = ROOT / "scope" / "rijksoverheid_invalid.txt"

INDEX_URL = "https://organisaties.overheid.nl/domeinen"

# Export kind -> the URL fragment that identifies its link on a category page.
EXPORTS = {
    "registraties": "export-domeinnaamregistraties",
    "domeinen":     "export-domeinen",
}

# Header of the end-date column in the registraties and domeinen export respectively.
END_DATE_COLUMNS = ("Einde registratie", "Einddatum")

# Delay between retries of a failed request, in seconds.
BACKOFF_SECONDS = (5, 15, 30)

_CATEGORY_HREF = re.compile(r'href="/domeinen/([A-Za-z_-]+)/?"')


def normalise(value: str) -> str:
    """Lower-case a register value and strip any scheme, wildcard and trailing slash."""
    value = value.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.removeprefix("*.")
    return value.rstrip("/")


def get_root_domain(value: str) -> str | None:
    host = value.split("/")[0].split(":")[0]
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return None


def get(session: requests.Session, url: str, timeout: int, attempts: int = 4) -> str | None:
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.exceptions.RequestException as exc:
            if attempt == attempts:
                print(f"  [FAIL] {url}: {exc}", flush=True)
                return None
            delay = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            print(f"  [RETRY] {url}: {exc} - retrying in {delay}s "
                  f"({attempt}/{attempts - 1})", flush=True)
            time.sleep(delay)
    return None


def discover_categories(session: requests.Session, timeout: int) -> list[str]:
    """Return the category slugs linked from the register's index page."""
    page = get(session, INDEX_URL, timeout)
    if page is None:
        raise RuntimeError(f"could not load {INDEX_URL}")
    slugs = list(dict.fromkeys(m.group(1) for m in _CATEGORY_HREF.finditer(page)))
    if not slugs:
        raise RuntimeError(f"no categories found on {INDEX_URL} - has the page layout changed?")
    return slugs


def has_ended(row: dict[str, str], today: date) -> bool:
    for column in END_DATE_COLUMNS:
        value = (row.get(column) or "").strip()
        if value:
            try:
                return datetime.strptime(value, "%d-%m-%Y").date() < today
            except ValueError:
                return False
    return False


def parse_export(text: str, kind: str, include_ended: bool) -> tuple[set[str], int]:
    """Return the root domains in one CSV export and how many ended rows were skipped."""
    domains: set[str] = set()
    skipped = 0
    today = date.today()
    reader = csv.DictReader(StringIO(text.lstrip("﻿")), delimiter=";")
    if not reader.fieldnames:
        return domains, skipped
    # The first column is the domain in both exports ("domeinnaamregistratie" / "Domein").
    domain_column = reader.fieldnames[0]
    for row in reader:
        if not include_ended and has_ended(row, today):
            skipped += 1
            continue
        value = normalise(row.get(domain_column) or "")
        root = get_root_domain(value)
        if not root:
            continue
        if kind == "domeinen" and value not in (root, "www." + root):
            continue
        domains.add(root)
    return domains, skipped


def fetch_category(session: requests.Session, slug: str, exports: tuple[str, ...],
                   include_ended: bool, timeout: int) -> set[str]:
    """Open one category page, follow its CSV export links and return the root domains."""
    category_url = f"{INDEX_URL}/{slug}/"
    page = get(session, category_url, timeout)
    if page is None:
        raise RuntimeError(f"could not load {category_url}")

    domains: set[str] = set()
    counts = []
    for kind in exports:
        match = re.search(rf'href="([^"]*/{EXPORTS[kind]}/[^"]+\.csv)"', page)
        # Fall back to the known URL shape if the link is not on the page.
        export_url = (urljoin(category_url, match.group(1)) if match
                      else f"{INDEX_URL}/{EXPORTS[kind]}/{slug}.csv")
        text = get(session, export_url, timeout)
        if text is None:
            raise RuntimeError(f"could not load {export_url}")
        found, skipped = parse_export(text, kind, include_ended)
        domains |= found
        counts.append(f"{kind} {len(found)}" + (f", {skipped} ended" if skipped else ""))

    print(f"  [ OK ] {slug}: {len(domains)} domains ({'; '.join(counts)})", flush=True)
    return domains


def fetch_domains(categories: tuple[str, ...] | None = None,
                  exports: tuple[str, ...] = tuple(EXPORTS),
                  include_ended: bool = False,
                  concurrency: int = 4, timeout: int = 30) -> set[str]:
    """Crawl the register and return the merged root-domain set.

    `categories` limits the crawl to those slugs; None means every category the
    index page lists. Raises if any page or export fails to load, so a caller
    never mistakes a partial register for the whole one.
    """
    with requests.Session() as session:
        session.headers["User-Agent"] = "DutchGovScope/1.0 (+https://github.com/zzzteph/DutchGovScope)"
        available = discover_categories(session, timeout)
        if categories is None:
            selected = available
        else:
            missing = [c for c in categories if c not in available]
            if missing:
                print(f"  [WARN] not on the index page, skipped: {', '.join(missing)}")
            selected = [c for c in categories if c in available]

        print(f"Fetching {len(selected)} of {len(available)} categories from {INDEX_URL} ...")
        entries: set[str] = set()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for result in pool.map(
                    lambda c: fetch_category(session, c, exports, include_ended, timeout),
                    selected):
                entries |= result
    print(f"  -> {len(entries)} unique root domains")
    return entries


def read_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def main():
    parser = argparse.ArgumentParser(
        description="Fetch the domains in the Register Internetdomeinen Overheid")
    parser.add_argument("output", nargs="?", help="File to write the domains to (one per line)")
    parser.add_argument("--categories",
                        help="Comma-separated category slugs, e.g. Ministeries,Politie "
                             "(default: every category on the index page)")
    parser.add_argument("--exports", default="both", choices=["both", *EXPORTS],
                        help="Which CSV export(s) to read per category (default: both)")
    parser.add_argument("--include-ended", action="store_true",
                        help="Keep rows whose registration or usage end date has passed")
    parser.add_argument("--exclude-known", action="store_true",
                        help="Drop domains already present in either scope file")
    parser.add_argument("--append", action="store_true",
                        help="Merge with the existing contents of the output file")
    parser.add_argument("--list-categories", action="store_true",
                        help="Print the category slugs on the index page and exit")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    if args.list_categories:
        with requests.Session() as session:
            print("\n".join(discover_categories(session, args.timeout)))
        return
    if not args.output:
        parser.error("output is required unless --list-categories is given")

    categories = None
    if args.categories:
        categories = tuple(c.strip() for c in args.categories.split(",") if c.strip())
    exports = tuple(EXPORTS) if args.exports == "both" else (args.exports,)

    domains = fetch_domains(categories, exports, args.include_ended,
                            args.concurrency, args.timeout)

    if args.exclude_known:
        known = read_set(VALID_FILE) | read_set(INVALID_FILE)
        before = len(domains)
        domains -= known
        print(f"  -> {len(domains)} new ({before - len(domains)} already in the scope files)")

    out_path = Path(args.output)
    if args.append:
        domains |= read_set(out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sorted(domains)) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(domains)} domains to {out_path}")


if __name__ == "__main__":
    main()
