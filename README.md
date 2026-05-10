# Dutch Government Bug Bounty Scope

Welcome to the repository dedicated to collecting and maintaining a precise list of the Dutch government's bug bounty scope. This includes domains and subdomains.  
*This is **NOT** an official bug bounty scope.*

To report a vulnerability or to learn more about Coordinated Vulnerability Disclosure (CVD), visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)


## Overview

This project aims to provide the **most accurate and detailed** list of domains and subdomains that are in scope of the Dutch government's bug bounty program. By mapping and monitoring relevant infrastructure, the goal is to support the security and visibility of government digital assets.

### What is in scope?

This repository focuses on verified, government-related resources. Each domain is included only after passing a multi-tier verification pipeline:

1. **HTTP + SSL signals**: Meta tags (`overheid:authority`, `rijksoverheid.org`), legal accessibility statements (`toegankelijkheidsverklaring.nl`), government analytics infrastructure, SSL certificate organisation field.
2. **Rendered DOM check**: Browser-rendered page (Playwright) to catch SPAs — same signal checks after JavaScript executes.
3. **Visual identity check**: Claude vision on a page screenshot — confirms the standard Rijksoverheid header (dark navy bar, Dutch coat of arms, pink stripe) or equivalent agency branding as the site's own identity.


### How It Works

All analysis runs via **GitHub Actions**. Results are stored as plain text files in the repository.

1. **Domain scope maintenance** — `engine/refresh_rijksoverheid.py`:
   - Monthly sync with the official [CommunicatieRijk websiteregister](https://www.communicatierijk.nl/vakkennis/r/rijkswebsites/verplichte-richtlijnen/websiteregister-rijksoverheid)
   - New domains are verified through the three-tier pipeline (`engine/verify_rijksoverheid.py`)
   - Confirmed domains → `scope/rijksoverheid.txt`; rejected/uncertain → `scope/rijksoverheid_invalid.txt`

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
- [communicatierijk.nl](https://www.communicatierijk.nl/vakkennis/r/rijkswebsites/verplichte-richtlijnen/websiteregister-rijksoverheid)  
- [ncsc.nl](https://www.ncsc.nl/contact/kwetsbaarheid-melden/cvd-meldingen-formulier)  
- [NCSC Wall of Fame](https://www.ncsc.nl/contact/kwetsbaarheid-melden/wall-of-fame)  

---

To report a vulnerability or learn more, please visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)
