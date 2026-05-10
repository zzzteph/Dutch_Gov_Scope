"""
Merge subfinder JSON output + dnsx-validated hostnames into per-domain storage files,
then regenerate the aggregate subdomains.txt files.

Usage:
    python process_subdomains.py <subfinder.jsonl> <dnsx_valid.txt> <storage/rijksoverheid>
"""
import json
import sys
from pathlib import Path
import tldextract


def root_domain(host: str) -> str | None:
    ext = tldextract.extract(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return None


def main():
    subfinder_file = Path(sys.argv[1])
    dnsx_file      = Path(sys.argv[2])
    storage        = Path(sys.argv[3])

    # Parse subfinder JSON lines (with -active flag all entries are DNS-validated)
    # Format: {"input":"root.nl","source":"...","host":"sub.root.nl"}
    # dnsx_file is kept as a parameter for backwards compatibility but ignored
    # when subfinder ran with -active (pass /dev/null to skip).
    extra_valid: set[str] = set()
    if dnsx_file.exists() and dnsx_file.name != "null":
        for line in dnsx_file.read_text(errors="replace").splitlines():
            h = line.strip().lower()
            if h:
                extra_valid.add(h)

    confirmed: dict[str, str] = {}
    if subfinder_file.exists():
        for line in subfinder_file.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                host         = obj.get("host", "").lower().strip()
                input_domain = obj.get("input", "").lower().strip()
                if host:
                    root = root_domain(input_domain) or root_domain(host)
                    if root:
                        confirmed[host] = root
            except (json.JSONDecodeError, KeyError):
                pass
    print(f"subfinder entries (DNS-validated via -active): {len(confirmed)}")

    # Merge any extra hosts from a separate dnsx pass
    for h in extra_valid:
        if h not in confirmed:
            r = root_domain(h)
            if r:
                confirmed[h] = r

    # Group by root domain
    by_domain: dict[str, set[str]] = {}
    for host, root in confirmed.items():
        by_domain.setdefault(root, set()).add(host)

    # Update per-domain storage files
    total_added = 0
    updated = 0
    for domain, new_subs in by_domain.items():
        domain_dir = storage / domain
        if not domain_dir.exists():
            continue  # only update domains that are already in scope
        sd_file = domain_dir / "subdomains.txt"
        existing: set[str] = set()
        if sd_file.exists():
            existing = {l.strip() for l in sd_file.read_text(errors="replace").splitlines() if l.strip()}
        merged = existing | new_subs
        added = len(merged) - len(existing)
        if added > 0:
            sd_file.write_text("\n".join(sorted(merged)) + "\n", encoding="utf-8")
            total_added += added
            updated += 1

    print(f"Updated {updated} domain dirs, {total_added} new subdomains added")

    # Regenerate aggregate files (deduplicated)
    all_subs: list[str] = []
    for entry in sorted(storage.iterdir()):
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
    (storage / "subdomains.txt").write_text(agg, encoding="utf-8")
    (storage.parent / "subdomains.txt").write_text(agg, encoding="utf-8")
    print(f"Aggregate subdomains.txt: {len(deduped)} total entries")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python process_subdomains.py <subfinder.jsonl> <dnsx_valid.txt> <storage/rijksoverheid>")
        sys.exit(1)
    main()
