"""
Resolve scope hosts to IPs, look up the announcing prefix on bgp.he.net, and add
the ranges that EXPLICITLY belong to the Dutch government to ip.txt.

Pipeline:

    1. RESOLVE   every host in the scope (and optionally every discovered
                 subdomain) to its A records
    2. LOOK UP   each unique IP on bgp.he.net -> origin ASN, announced prefix and
                 the prefix description
    3. MATCH     keep only prefixes whose description EXPLICITLY names a Dutch
                 government body ("Ministerie van ...", "Rijkswaterstaat",
                 "Politie", "Staat der Nederlanden", ...)
    4. WRITE     merge the surviving CIDRs into ip.txt

The match is deliberately literal. A range is only accepted when its own
description says it is Dutch government -- a government site hosted at AWS,
Azure, KPN or any other provider does NOT make that provider's range government
owned, so those are skipped no matter how many scope hosts point at them.

Lookups are collapsed by prefix: once 145.12.0.0/24 is known, every other IP
inside it is resolved from cache, so a few thousand IPs cost a few hundred
requests.

Usage:
    python engine/resolve_asn.py                     # dry run, scope roots only
    python engine/resolve_asn.py --apply             # actually write ip.txt
    python engine/resolve_asn.py --source all        # roots + discovered subdomains
    python engine/resolve_asn.py --source storage --apply
    python engine/resolve_asn.py --limit 500 --concurrency 40
"""

import argparse
import html
import ipaddress
import json
import re
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT        = Path(__file__).parent.parent
SCOPE_FILE  = ROOT / "scope" / "rijksoverheid.txt"
STORAGE_AGG = ROOT / "storage" / "subdomains.txt"
IP_FILE     = ROOT / "ip.txt"

HE_IP_URL = "https://bgp.he.net/ip/{ip}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DutchGovScope/1.0"

# A prefix is accepted ONLY when its bgp.he.net description matches one of these.
# Every entry names a Dutch government body outright -- no provider names, no
# inference from hosting. Keep this list literal.
GOV_PATTERNS = (
    r"\bministerie\b",
    r"\bministry of\b.*\bnetherlands\b",
    r"\brijksoverheid\b",
    r"\bstaat der nederlanden\b",
    r"\brijkswaterstaat\b",
    r"\bbelastingdienst\b",
    r"\brijksdienst\b",
    r"\bpolitie\b",
    r"\bkoninklijke marechaussee\b",
    r"\bdefensie\b",
    r"\bopenbaar ministerie\b",
    r"\bhoge raad\b",
    r"\btweede kamer\b",
    r"\beerste kamer\b",
    r"\braad van state\b",
    r"\balgemene rekenkamer\b",
    r"\bnationale politie\b",
    r"\bdienst justitiele inrichtingen\b",
    r"\bjustitiele informatiedienst\b",
    r"\bkadaster\b",
    r"\blogius\b",
    # Named executive agencies that carry no "rijks"/"ministerie" prefix. Each is
    # a Dutch government body in its own right, seen in real bgp.he.net output.
    r"\bdienst uitvoering onderwijs\b",   # DUO, agency of OCW
    r"\bssc-ict\b",                       # shared ICT service centre of the ministries
    r"\bdienst wegverkeer\b",             # RDW, national vehicle authority
)

# Descriptions that do not match GOV_PATTERNS but look Dutch/public-sector are
# surfaced in the report so a human can extend GOV_PATTERNS deliberately.
NEAR_MISS_HINTS = ("nederland", "dutch", "overheid", "gemeente", "provincie",
                   "waterschap", "rijks", "staat", "government")

GOV_RE = re.compile("|".join(GOV_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l.strip().lower() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.strip()]


def load_hosts(source: str) -> list[str]:
    hosts: set[str] = set()
    if source in ("scope", "all"):
        found = read_lines(SCOPE_FILE)
        print(f"  scope/rijksoverheid.txt : {len(found)} hosts")
        hosts |= set(found)
    if source in ("storage", "all"):
        found = read_lines(STORAGE_AGG)
        print(f"  storage/subdomains.txt  : {len(found)} hosts")
        hosts |= set(found)
    if source not in ("scope", "storage", "all"):
        path = Path(source)
        if not path.is_absolute():
            path = ROOT / source
        if not path.exists():
            print(f"Error: source file {path} not found")
            sys.exit(1)
        found = read_lines(path)
        print(f"  {path} : {len(found)} hosts")
        hosts |= set(found)
    return sorted(hosts)


# ---------------------------------------------------------------------------
# Step 1 -- DNS resolution
# ---------------------------------------------------------------------------

def resolve_host(host: str) -> tuple[str, set[str]]:
    """Return every IPv4 address the host resolves to."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET)
    except (socket.gaierror, UnicodeError, OSError):
        return host, set()
    return host, {info[4][0] for info in infos}


def resolve_all(hosts: list[str], concurrency: int) -> dict[str, set[str]]:
    print(f"\nResolving {len(hosts)} hosts (concurrency {concurrency}) ...")
    ip_to_hosts: dict[str, set[str]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for host, ips in pool.map(resolve_host, hosts):
            done += 1
            for ip in ips:
                ip_to_hosts.setdefault(ip, set()).add(host)
            if done % 500 == 0:
                print(f"  {done}/{len(hosts)} resolved, {len(ip_to_hosts)} unique IPs", flush=True)
    print(f"  {done}/{len(hosts)} resolved -> {len(ip_to_hosts)} unique IPv4 addresses")
    return ip_to_hosts


# ---------------------------------------------------------------------------
# Step 2 -- bgp.he.net lookup
# ---------------------------------------------------------------------------

# The "Announced By" table row: AS link, prefix link, then the description cell.
ANNOUNCE_RE = re.compile(
    r'href="/AS(?P<asn>\d+)".*?href="/net/(?P<prefix>[^"]+)".*?'
    r'<td[^>]*>(?P<descr>.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)


def he_lookup(session: requests.Session, ip: str, timeout: int,
              attempts: int = 3) -> dict | None:
    """Return {asn, prefix, description} for an IP, or None if unannounced."""
    url = HE_IP_URL.format(ip=ip)
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 429:
                raise requests.exceptions.RequestException("429 rate limited")
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as exc:
            if attempt == attempts:
                return {"ip": ip, "error": str(exc)[:120]}
            time.sleep(3 * attempt)
    else:
        return {"ip": ip, "error": "exhausted retries"}

    match = ANNOUNCE_RE.search(response.text)
    if not match:
        return None

    descr = html.unescape(re.sub(r"<[^>]+>", "", match.group("descr"))).strip()
    return {
        "ip": ip,
        "asn": f"AS{match.group('asn')}",
        "prefix": html.unescape(match.group("prefix")),
        "description": descr,
    }


def lookup_prefixes(ips: list[str], concurrency: int, timeout: int,
                    delay: float) -> tuple[dict[str, dict], list[dict]]:
    """
    Look up each IP, reusing an already-known prefix whenever the IP falls inside
    one. Returns (prefix -> record, errors).
    """
    known: dict[str, dict] = {}          # prefix string -> record
    networks: list[tuple[ipaddress.IPv4Network, str]] = []
    errors: list[dict] = []
    pending: list[str] = []

    def covered(ip: str) -> bool:
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net, _ in networks)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.verify = False

    # Sorting groups neighbouring addresses, so the prefix cache hits early and often.
    ordered = sorted(ips, key=lambda x: int(ipaddress.ip_address(x)))
    print(f"\nLooking up {len(ordered)} unique IPs on bgp.he.net "
          f"(prefix-cached, concurrency {concurrency}) ...")

    def flush(batch: list[str]) -> None:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda i: he_lookup(session, i, timeout), batch))
        for rec in results:
            if rec is None:
                continue
            if "error" in rec:
                errors.append(rec)
                continue
            if rec["prefix"] not in known:
                known[rec["prefix"]] = rec
                try:
                    networks.append((ipaddress.ip_network(rec["prefix"], strict=False),
                                     rec["prefix"]))
                except ValueError:
                    pass

    for ip in ordered:
        if covered(ip):
            continue
        pending.append(ip)
        if len(pending) >= concurrency:
            flush(pending)
            pending = []
            print(f"  {len(known)} distinct prefixes found, {len(errors)} errors", flush=True)
            if delay:
                time.sleep(delay)
    if pending:
        flush(pending)

    print(f"  -> {len(known)} distinct prefixes, {len(errors)} lookup errors")
    return known, errors


# ---------------------------------------------------------------------------
# Step 3 -- explicit Dutch government match
# ---------------------------------------------------------------------------

def is_explicit_gov(description: str) -> bool:
    return bool(GOV_RE.search(description or ""))


def is_near_miss(description: str) -> bool:
    low = (description or "").lower()
    return any(hint in low for hint in NEAR_MISS_HINTS)


# ---------------------------------------------------------------------------
# Step 4 -- write ip.txt
# ---------------------------------------------------------------------------

def parse_nets(cidrs) -> list[ipaddress.IPv4Network]:
    nets = []
    for c in cidrs:
        try:
            nets.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            pass
    return nets


def find_redundant(cidrs: set[str]) -> list[tuple[str, str]]:
    """Return (subsumed, covering) pairs -- ranges made redundant by a wider one."""
    nets = sorted(parse_nets(cidrs), key=lambda n: n.prefixlen)
    redundant = []
    for i, small in enumerate(nets):
        for big in nets:
            if big is not small and big.prefixlen < small.prefixlen and small.subnet_of(big):
                redundant.append((str(small), str(big)))
                break
    return redundant


def merge_ip_file(new_cidrs: set[str], apply: bool,
                  collapse: bool) -> tuple[int, int, set[str], list[tuple[str, str]]]:
    existing = set(read_lines(IP_FILE))
    added = {c for c in new_cidrs if c not in existing}
    merged = existing | new_cidrs
    redundant = find_redundant(merged)

    if collapse:
        merged = {str(n) for n in ipaddress.collapse_addresses(parse_nets(merged))}

    def sort_key(cidr: str):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            return (0, int(net.network_address), net.prefixlen)
        except ValueError:
            return (1, 0, 0)

    if apply:
        IP_FILE.write_text("\n".join(sorted(merged, key=sort_key)) + "\n",
                           encoding="utf-8", newline="\n")
    return len(existing), len(merged), added, redundant


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Resolve scope hosts, check their ASN on bgp.he.net, and add "
                    "explicitly Dutch-government ranges to ip.txt")
    parser.add_argument("--source", default="scope",
                        help="scope | storage | all | path to a host file (default: scope)")
    parser.add_argument("--apply", action="store_true",
                        help="Write the matched ranges into ip.txt (default: report only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Resolve at most N hosts (0 = no limit)")
    parser.add_argument("--concurrency", type=int, default=12,
                        help="Parallel bgp.he.net lookups (keep modest - be polite)")
    parser.add_argument("--dns-concurrency", type=int, default=60,
                        help="Parallel DNS resolutions (cheap, dead hosts block on timeout)")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--reuse", metavar="PREFIXES_JSONL",
                        help="Re-classify a previous run's prefixes.jsonl instead of "
                             "resolving and querying again -- use this after editing "
                             "GOV_PATTERNS (no DNS, no bgp.he.net traffic)")
    parser.add_argument("--collapse", action="store_true",
                        help="Collapse ip.txt into minimal CIDRs, dropping ranges that a "
                             "wider range already covers")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds to pause between bgp.he.net batches (be polite)")
    parser.add_argument("--output-dir", default="verification_results/asn",
                        help="Where the report is written")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # --reuse re-classifies a previous run's prefixes.jsonl. Tuning GOV_PATTERNS
    # then costs nothing: no DNS, no bgp.he.net traffic.
    if args.reuse:
        cache = Path(args.reuse)
        if not cache.is_absolute():
            cache = ROOT / args.reuse
        if not cache.exists():
            print(f"Error: {cache} not found")
            sys.exit(1)
        cached = [json.loads(l) for l in
                  cache.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Re-classifying {len(cached)} cached prefixes from {cache}")
        prefixes = {r["prefix"]: r for r in cached}
        ip_to_hosts, errors = {}, []
        hosts_by_prefix = {r["prefix"]: set(range(r.get("hosts", 0))) for r in cached}
        report_from(prefixes, hosts_by_prefix, ip_to_hosts, errors, args, output_dir)
        return

    print("Loading hosts ...")
    hosts = load_hosts(args.source)
    if args.limit and args.limit < len(hosts):
        print(f"  limiting to the first {args.limit} of {len(hosts)} hosts")
        hosts = hosts[:args.limit]

    ip_to_hosts = resolve_all(hosts, args.dns_concurrency)
    if not ip_to_hosts:
        print("\nNothing resolved. Stopping.")
        return

    prefixes, errors = lookup_prefixes(list(ip_to_hosts), args.concurrency,
                                       args.timeout, args.delay)

    # Attribute each prefix back to the hosts that live inside it.
    nets = []
    for cidr in prefixes:
        try:
            nets.append((ipaddress.ip_network(cidr, strict=False), cidr))
        except ValueError:
            pass
    hosts_by_prefix: dict[str, set[str]] = {c: set() for c in prefixes}
    for ip, ip_hosts in ip_to_hosts.items():
        addr = ipaddress.ip_address(ip)
        for net, cidr in nets:
            if addr in net:
                hosts_by_prefix[cidr] |= ip_hosts
                break

    report_from(prefixes, hosts_by_prefix, ip_to_hosts, errors, args, output_dir)


def report_from(prefixes, hosts_by_prefix, ip_to_hosts, errors, args, output_dir):
    gov, near, other = [], [], []
    for cidr, rec in prefixes.items():
        rec = {**rec, "hosts": len(hosts_by_prefix.get(cidr, ()))}
        if is_explicit_gov(rec["description"]):
            gov.append(rec)
        elif is_near_miss(rec["description"]):
            near.append(rec)
        else:
            other.append(rec)

    gov.sort(key=lambda r: -r["hosts"])
    near.sort(key=lambda r: -r["hosts"])
    other.sort(key=lambda r: -r["hosts"])

    gov_cidrs = {r["prefix"] for r in gov}
    before, after, added, redundant = merge_ip_file(gov_cidrs, args.apply, args.collapse)

    # ---- report ----------------------------------------------------------
    print(f"\n{'-' * 62}")
    print(f"  Unique IPs resolved        : {len(ip_to_hosts):>5}")
    print(f"  Distinct announced prefixes: {len(prefixes):>5}")
    print(f"  EXPLICIT Dutch government  : {len(gov):>5}")
    print(f"  Near misses (review)       : {len(near):>5}")
    print(f"  Other / third-party        : {len(other):>5}")
    print(f"  Lookup errors              : {len(errors):>5}")
    print(f"{'-' * 62}")
    print(f"  ip.txt {before} -> {after} ranges  ({len(added)} new)")
    if not args.apply:
        print("  DRY RUN - ip.txt not written. Re-run with --apply.")
    if redundant:
        print(f"\n{len(redundant)} range(s) in ip.txt are already covered by a "
              f"wider range (use --collapse to remove):")
        for small, big in redundant[:10]:
            print(f"  {small:<20} covered by {big}")
        if len(redundant) > 10:
            print(f"  ... and {len(redundant) - 10} more")

    if gov:
        print("\nExplicitly Dutch government (top 20 by host count):")
        for r in gov[:20]:
            print(f"  {r['prefix']:<20} {r['asn']:<10} {r['hosts']:>5} hosts  {r['description']}")
    if near:
        print("\nNear misses - NOT added, extend GOV_PATTERNS if any belong (top 20):")
        for r in near[:20]:
            print(f"  {r['prefix']:<20} {r['asn']:<10} {r['hosts']:>5} hosts  {r['description']}")

    (output_dir / "prefixes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in gov + near + other) + "\n",
        encoding="utf-8", newline="\n")
    (output_dir / "gov_ranges.txt").write_text(
        "\n".join(sorted(gov_cidrs)) + "\n", encoding="utf-8", newline="\n")
    if errors:
        (output_dir / "errors.jsonl").write_text(
            "\n".join(json.dumps(e) for e in errors) + "\n",
            encoding="utf-8", newline="\n")
    print(f"\nReport: {output_dir}")


if __name__ == "__main__":
    main()
