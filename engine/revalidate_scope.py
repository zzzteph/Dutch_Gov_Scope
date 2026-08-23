"""
Global re-validation of the top-level domains in the scope files.

Takes every domain in scope/rijksoverheid.txt and scope/rijksoverheid_invalid.txt,
runs it back through the three-tier pipeline in verify_rijksoverheid.py
(raw HTTP + SSL -> rendered DOM -> Claude vision), and re-sorts the two files
according to the fresh verdicts.

This is the maintenance counterpart to refresh_rijksoverheid.py:

    refresh_rijksoverheid.py   pulls NEW domains from communicatierijk.nl
    basisbeveiliging.py        harvests candidate domains from basisbeveiliging.nl
    revalidate_scope.py        re-checks domains that are ALREADY in the scope files

Reconciliation rules
--------------------
    current      verdict          action
    ---------    -------------    ---------------------------------------------
    valid        confirmed        stays valid
    valid        rejected         DEMOTED  -> rijksoverheid_invalid.txt
    valid        manual_review    stays valid   (--strict demotes instead)
    invalid      confirmed        PROMOTED -> rijksoverheid.txt
    invalid      rejected         stays invalid
    invalid      manual_review    stays invalid
    unknown      confirmed        ADDED    -> rijksoverheid.txt
    unknown      rejected|review  ADDED    -> rijksoverheid_invalid.txt

A domain that is currently in scope is never dropped on a *manual_review* verdict
by default: a timeout, a WAF block or a slow SPA is not evidence that a domain
stopped belonging to the government. Use --strict to demote those too.

Usage:
    python engine/revalidate_scope.py                          # both files, full pipeline
    python engine/revalidate_scope.py --source valid           # only the confirmed scope
    python engine/revalidate_scope.py --source invalid         # only the reject pile
    python engine/revalidate_scope.py --source bb_candidates.txt
    python engine/revalidate_scope.py --with-basisbeveiliging  # + fresh harvested candidates
    python engine/revalidate_scope.py --no-vision --concurrency 30
    python engine/revalidate_scope.py --limit 200 --skip-recent 30
    python engine/revalidate_scope.py --dry-run                # report, write nothing
    python engine/revalidate_scope.py --prune-storage          # drop demoted domains from storage/
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT          = Path(__file__).parent.parent
SCOPE_DIR     = ROOT / "scope"
VALID_FILE    = SCOPE_DIR / "rijksoverheid.txt"
INVALID_FILE  = SCOPE_DIR / "rijksoverheid_invalid.txt"
LEDGER_FILE   = SCOPE_DIR / "verification_log.jsonl"
STORAGE_DIR   = ROOT / "storage" / "rijksoverheid"
VERIFY_SCRIPT = Path(__file__).parent / "verify_rijksoverheid.py"

RULE = "-" * 58


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def read_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()}


def write_sorted(path: Path, domains: set[str]) -> None:
    # newline="\n" is required: without it Python's text mode rewrites these
    # LF-terminated files with CRLF on Windows.
    path.write_text("\n".join(sorted(domains)) + "\n", encoding="utf-8", newline="\n")


def read_ledger() -> dict[str, dict]:
    """Last known verdict per domain (later lines win)."""
    if not LEDGER_FILE.exists():
        return {}
    entries: dict[str, dict] = {}
    for line in LEDGER_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("domain"):
            entries[rec["domain"]] = rec
    return entries


def append_ledger(records: list[dict]) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LEDGER_FILE.open("a", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps({**rec, "checked_at": stamp}) + "\n")


def days_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def select_targets(args, valid: set[str], invalid: set[str]) -> list[str]:
    if args.source == "valid":
        targets = set(valid)
    elif args.source == "invalid":
        targets = set(invalid)
    elif args.source == "all":
        targets = valid | invalid
    else:
        path = Path(args.source)
        if not path.is_absolute():
            path = ROOT / args.source
        if not path.exists():
            print(f"Error: source file {path} not found")
            sys.exit(1)
        targets = read_set(path)

    if args.with_basisbeveiliging:
        from basisbeveiliging import fetch_candidates
        harvested = fetch_candidates(concurrency=args.concurrency)
        fresh = harvested - valid - invalid
        print(f"basisbeveiliging.nl: {len(harvested)} domains, {len(fresh)} not yet in the scope files")
        targets |= harvested

    if args.skip_recent > 0:
        ledger = read_ledger()
        before = len(targets)
        targets = {d for d in targets
                   if days_since(ledger.get(d, {}).get("checked_at")) >= args.skip_recent}
        if before - len(targets):
            print(f"Skipping {before - len(targets)} domain(s) verified in the last "
                  f"{args.skip_recent} day(s)")

    ordered = sorted(targets)
    if args.limit and args.limit < len(ordered):
        print(f"Limiting run to the first {args.limit} of {len(ordered)} target domain(s)")
        ordered = ordered[:args.limit]
    return ordered


# ---------------------------------------------------------------------------
# Verifier invocation
# ---------------------------------------------------------------------------

def run_verifier(domains: list[str], output_dir: Path, args) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_file = output_dir / "_input.txt"
    input_file.write_text("\n".join(domains) + "\n", encoding="utf-8", newline="\n")

    cmd = [
        sys.executable, str(VERIFY_SCRIPT),
        str(input_file),
        "--output-dir", str(output_dir),
        "--concurrency", str(args.concurrency),
    ]
    if args.no_vision:
        cmd.append("--no-vision")

    print(f"\nRunning: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)

    details = output_dir / "details.jsonl"
    if not details.exists():
        print("Error: verifier produced no details.jsonl")
        sys.exit(1)

    return [json.loads(l) for l in
            details.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile(results: list[dict], valid: set[str], invalid: set[str], strict: bool):
    """Return (new_valid, new_invalid, changes) without touching disk."""
    new_valid   = set(valid)
    new_invalid = set(invalid)
    changes = {"promoted": [], "demoted": [], "added_valid": [], "added_invalid": [], "unchanged": []}

    for rec in results:
        domain = rec["domain"].lower()
        status = rec["status"]
        was_valid   = domain in valid
        was_invalid = domain in invalid
        entry = {"domain": domain, "status": status,
                 "tier": rec.get("tier"), "reason": rec.get("reason", "")}

        if status == "confirmed":
            if was_valid:
                changes["unchanged"].append(entry)
            else:
                new_valid.add(domain)
                new_invalid.discard(domain)
                (changes["promoted"] if was_invalid else changes["added_valid"]).append(entry)

        elif status == "rejected":
            if was_invalid:
                changes["unchanged"].append(entry)
            else:
                new_invalid.add(domain)
                new_valid.discard(domain)
                (changes["demoted"] if was_valid else changes["added_invalid"]).append(entry)

        else:  # manual_review
            if was_valid and strict:
                new_valid.discard(domain)
                new_invalid.add(domain)
                changes["demoted"].append(entry)
            elif was_valid or was_invalid:
                changes["unchanged"].append(entry)
            else:
                new_invalid.add(domain)
                changes["added_invalid"].append(entry)

    return new_valid, new_invalid, changes


# ---------------------------------------------------------------------------
# Storage pruning (opt-in)
# ---------------------------------------------------------------------------

def regenerate_aggregates() -> int:
    all_subs: list[str] = []
    for entry in sorted(STORAGE_DIR.iterdir()):
        if entry.is_dir():
            sd = entry / "subdomains.txt"
            if sd.exists():
                all_subs.extend(sd.read_text(errors="replace").splitlines())

    seen: set[str] = set()
    deduped: list[str] = []
    for s in all_subs:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)

    agg = "\n".join(deduped) + "\n"
    (STORAGE_DIR / "subdomains.txt").write_text(agg, encoding="utf-8", newline="\n")
    (STORAGE_DIR.parent / "subdomains.txt").write_text(agg, encoding="utf-8", newline="\n")
    return len(deduped)


def prune_storage(demoted: list[dict]) -> int:
    """Remove storage dirs of demoted domains, then rebuild the aggregate files."""
    removed = 0
    for entry in demoted:
        target = STORAGE_DIR / entry["domain"]
        if target.is_dir():
            shutil.rmtree(target)
            removed += 1
    if removed:
        total = regenerate_aggregates()
        print(f"Pruned {removed} storage dir(s); aggregate subdomains.txt now {total} entries")
    return removed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_report(output_dir: Path, changes: dict, results: list[dict],
                 valid_count: int, invalid_count: int, dry_run: bool) -> Path:
    summary = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "domains_checked": len(results),
        "verdicts": {
            "confirmed":     sum(1 for r in results if r["status"] == "confirmed"),
            "rejected":      sum(1 for r in results if r["status"] == "rejected"),
            "manual_review": sum(1 for r in results if r["status"] == "manual_review"),
        },
        "changes": {k: len(v) for k, v in changes.items()},
        "scope_after": {"valid": valid_count, "invalid": invalid_count},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8", newline="\n")

    lines = [f"# Scope revalidation - {summary['checked_at']}", ""]
    if dry_run:
        lines += ["> DRY RUN - no scope files were modified.", ""]
    lines += [
        f"Checked **{len(results)}** top-level domains - "
        f"{summary['verdicts']['confirmed']} confirmed, "
        f"{summary['verdicts']['rejected']} rejected, "
        f"{summary['verdicts']['manual_review']} manual review.",
        "",
    ]
    for key, title in [
        ("demoted",       "Demoted - were in scope, now rejected"),
        ("promoted",      "Promoted - were rejected, now confirmed"),
        ("added_valid",   "Added to scope"),
        ("added_invalid", "Added to invalid"),
    ]:
        if changes[key]:
            lines += [f"## {title} ({len(changes[key])})", ""]
            for e in sorted(changes[key], key=lambda x: x["domain"]):
                lines.append(f"- `{e['domain']}` - tier {e['tier']}: {e['reason']}")
            lines.append("")
    if not any(changes[k] for k in ("demoted", "promoted", "added_valid", "added_invalid")):
        lines += ["No scope changes - every checked domain kept its current classification.", ""]
    lines += [f"Scope after: **{valid_count}** valid / **{invalid_count}** invalid.", ""]

    report = output_dir / "changes.md"
    report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Re-validate scope domains and re-sort them between the two scope files")
    parser.add_argument("--source", default="all",
                        help="valid | invalid | all | path to a file of domains (default: all)")
    parser.add_argument("--with-basisbeveiliging", action="store_true",
                        help="Also pull fresh candidate domains from basisbeveiliging.nl")
    parser.add_argument("--limit", type=int, default=0,
                        help="Check at most N domains this run (0 = no limit)")
    parser.add_argument("--skip-recent", type=int, default=0, metavar="DAYS",
                        help="Skip domains already verified within the last N days")
    parser.add_argument("--concurrency", type=int, default=15)
    parser.add_argument("--no-vision", action="store_true",
                        help="Tier 1 only - no browser render, no Claude vision")
    parser.add_argument("--strict", action="store_true",
                        help="Demote in-scope domains that come back as manual_review")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing the scope files")
    parser.add_argument("--prune-storage", action="store_true",
                        help="Delete storage/rijksoverheid/<domain>/ for demoted domains "
                             "and rebuild the aggregates")
    parser.add_argument("--output-dir", default="verification_results/revalidation",
                        help="Where verifier output and the report are written")
    args = parser.parse_args()

    valid   = read_set(VALID_FILE)
    invalid = read_set(INVALID_FILE)
    print(f"scope/rijksoverheid.txt         : {len(valid)} domains")
    print(f"scope/rijksoverheid_invalid.txt : {len(invalid)} domains")

    targets = select_targets(args, valid, invalid)
    if not targets:
        print("\nNothing to verify.")
        return
    print(f"Targets this run                : {len(targets)} domains")

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / args.output_dir

    results = run_verifier(targets, output_dir, args)
    new_valid, new_invalid, changes = reconcile(results, valid, invalid, args.strict)

    print(f"\n{RULE}")
    print(f"  Promoted (invalid -> valid) : {len(changes['promoted']):>4}")
    print(f"  Demoted  (valid -> invalid) : {len(changes['demoted']):>4}")
    print(f"  Added to valid              : {len(changes['added_valid']):>4}")
    print(f"  Added to invalid            : {len(changes['added_invalid']):>4}")
    print(f"  Unchanged                   : {len(changes['unchanged']):>4}")
    print(RULE)

    for key, label in [("demoted", "DEMOTED "), ("promoted", "PROMOTED")]:
        for e in sorted(changes[key], key=lambda x: x["domain"]):
            print(f"  [{label}] {e['domain']} - {e['reason']}")

    if args.dry_run:
        print("\nDRY RUN - scope files left untouched.")
    else:
        write_sorted(VALID_FILE, new_valid)
        write_sorted(INVALID_FILE, new_invalid)
        append_ledger(results)
        print(f"\nscope/rijksoverheid.txt         : {len(new_valid)} domains "
              f"({len(new_valid) - len(valid):+d})")
        print(f"scope/rijksoverheid_invalid.txt : {len(new_invalid)} domains "
              f"({len(new_invalid) - len(invalid):+d})")
        if args.prune_storage and changes["demoted"]:
            prune_storage(changes["demoted"])

    report = write_report(output_dir, changes, results,
                          len(new_valid), len(new_invalid), args.dry_run)
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
