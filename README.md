# Dutch Government Bug Bounty Scope

Welcome to the repository dedicated to collecting and maintaining a precise list of the Dutch government's bug bounty scope. This includes domains and subdomains.  
*This is **NOT** an official bug bounty scope.*

To report a vulnerability or to learn more about Coordinated Vulnerability Disclosure (CVD), visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)


## Overview

This project aims to provide the **most accurate and detailed** list of domains and subdomains that are in scope of the Dutch government's bug bounty program. By mapping and monitoring relevant infrastructure, the goal is to support the security and visibility of government digital assets.

### What is in scope?

This repository focuses on verified, government-related resources. Domains listed in the government's own
[Register Internetdomeinen Overheid](https://organisaties.overheid.nl/domeinen) are in scope as-is. Every other
domain is included only after passing a multi-tier verification pipeline:

1. **HTTP + SSL signals**: Meta tags (`overheid:authority`, `rijksoverheid.org`), legal accessibility statements (`toegankelijkheidsverklaring.nl`), government analytics infrastructure, SSL certificate organisation field.
2. **Rendered DOM check**: Browser-rendered page (Playwright) to catch SPAs — same signal checks after JavaScript executes.
3. **Visual identity check**: Claude vision on a page screenshot — confirms the standard Rijksoverheid header (dark navy bar, Dutch coat of arms, pink stripe) or equivalent agency branding as the site's own identity.


### How It Works

All analysis runs via **GitHub Actions**. Results are stored as plain text files in the repository.

1. **Domain scope maintenance** — `engine/refresh_rijksoverheid.py`:
   - Monthly sync with the official [Register Internetdomeinen Overheid](https://organisaties.overheid.nl/domeinen)
     (successor of the CommunicatieRijk websiteregister) via `engine/overheid_domeinen.py`, which walks every
     category and reads its *Domeinnaamregistraties* and *Domeinen* CSV exports
   - Register domains go straight into `scope/rijksoverheid.txt` without verification, and are removed from
     `scope/rijksoverheid_invalid.txt`. Lapsed registrations and hosts on vendor platforms
     (`almere.notubiz.nl` → `notubiz.nl`) are left out.

   Candidate domains also come from [basisbeveiliging.nl](https://basisbeveiliging.nl/) via
   `engine/basisbeveiliging.py`, which only *harvests* root domains — it never decides
   ownership itself. Those candidates are verified through the three-tier pipeline
   (`engine/verify_rijksoverheid.py`): confirmed → `scope/rijksoverheid.txt`,
   rejected/uncertain → `scope/rijksoverheid_invalid.txt`.

1. **IP range attribution** — `engine/resolve_asn.py`:
   - Resolves scope hosts to IPs, looks each announcing prefix up on [bgp.he.net](https://bgp.he.net/)
   - Adds a range to [`ip.txt`](ip.txt) **only** when the prefix description explicitly names a
     Dutch government body (`Ministerie van …`, `Rijkswaterstaat`, `Politie`, …). A government
     site hosted at AWS or KPN does not make that provider's range government-owned.
   - Near misses are reported rather than silently dropped, so the match list stays deliberate

   ```
   python engine/resolve_asn.py --source all --apply
   ```

1. **Scope re-validation** — `engine/revalidate_scope.py`:
   - Runs the whole chain in one pass: domain register → collect candidates → verify → reconcile → ASN sweep
   - Re-runs the three-tier pipeline over the top-level domains **already** in both scope files,
     except register domains, which are never verified or demoted
   - Re-sorts them on the fresh verdicts: rejected in-scope domains are demoted, confirmed
     rejects are promoted back. A `manual_review` verdict never drops a domain that is
     currently in scope — a timeout is not evidence of non-ownership.
   - Every run appends to `scope/verification_log.jsonl` and writes a `changes.md` report

   ```
   python engine/revalidate_scope.py --source all --concurrency 20
   python engine/revalidate_scope.py --source bb_candidates.txt --dry-run
   ```

2. **Subdomain discovery** — runs daily via GitHub Actions:
   - Subfinder with inline DNS validation (`-active`) on a rotating 3% slice of scope (with overlap)
   - Results merged into per-domain storage files and aggregated


### Repository Structure

- [`scope/rijksoverheid.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/scope/rijksoverheid.txt) – Verified **Rijksoverheid** root domains
- [`storage/subdomains.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/subdomains.txt) – All discovered subdomains (combined)
- [`storage/rijksoverheid/subdomains.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/subdomains.txt) – Subdomains under **Rijksoverheid** domains


### Scanning examples

```
curl --silent https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/subdomains.txt | ./nuclei -silent -id geoserver-login-panel
```

```
curl --silent https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/subdomains.txt | ./nuclei -silent -id exposure -severity critical,high
```

#### Scanning via Docker

```
curl --silent https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/subdomains.txt -o subdomains.txt && docker run -v "$PWD:/data" --rm projectdiscovery/nuclei -silent -id geoserver-login-panel -l /data/subdomains.txt
```


## Links and Acknowledgements

- [Bug Bounty Dutch Government Scope – Gist](https://gist.github.com/zzzteph/99a7bd2acde12cb4b2626fc9261bc56d)  
- [basisbeveiliging.nl](https://basisbeveiliging.nl/)  
- [overheid.nl](https://www.overheid.nl/english/dutch-government-websites)  
- [Register Internetdomeinen Overheid](https://organisaties.overheid.nl/domeinen)  
- [ncsc.nl](https://www.ncsc.nl/contact/kwetsbaarheid-melden/cvd-meldingen-formulier)  
- [NCSC Wall of Fame](https://www.ncsc.nl/contact/kwetsbaarheid-melden/wall-of-fame)  

---

To report a vulnerability or learn more, please visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)
