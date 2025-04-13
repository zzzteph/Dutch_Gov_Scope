# Dutch Government Bug Bounty Scope

Welcome to the repository dedicated to collecting and maintaining a precise list of the Dutch government's bug bounty scope. This includes domains, subdomains, and endpoints (URLs).  
*This is **NOT** an official bug bounty scope.*

To report a vulnerability or to learn more about Coordinated Vulnerability Disclosure (CVD), visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)


## Overview

This project aims to provide the **most accurate and detailed** list of domains and endpoints that are in scope of the Dutch government’s bug bounty program. By mapping and monitoring relevant infrastructure, the goal is to support the security and visibility of government digital assets.

### What is in scope?

This repository focuses on verified, government-related resources. The scope list is built to ensure **it is the most accurate list available**.  

Each resource is included only if it meets the following criteria:

1. **Meta tag**: The page must include the `RIJKSOVERHEID.Organisatie` meta information.  
2. **Official logo**: The site must display the Dutch government logo.  
3. **Clear affiliation**: There must be a clear mention of the official government body or agency.


### How It Works

The data is collected using a semi-automated pipeline and updated regularly. All analysis is done via **Github actions** and results are stored and synced in the `scan_scope.db` SQLite database. The process includes:

1. **Manual updates**: Input resources are stored in the `/scope` folder.  
2. **Weekly sync with CommunicatieRijk** data.  
3. **Automated collection per domain**:
   - **Subdomain discovery**: Using Amass, Subfinder, and DNSX for validation.  
   - **Endpoint discovery**: HTTPX filters and confirms valid HTTP/HTTPS endpoints.



### Repository Structure

- [`scope/rijksoverheid.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/scope/rijksoverheid.txt) – Current list of **Rijksoverheid** domains  
- [`storage/subdomains.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/subdomains.txt) – Combined list of all discovered subdomains (Rijksoverheid)  
- [`storage/endpoints.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/endpoints.txt) – Discovered HTTP/HTTPS endpoints  
- [`storage/wordlist.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/wordlist.txt) – Wordlist for subdomain bruteforcing, generated from known subdomains  
- [`storage/rijksoverheid/subdomains.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/subdomains.txt) – Subdomains related to **Rijksoverheid**  
- [`storage/rijksoverheid/endpoints.txt`](https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/endpoints.txt) – HTTP/HTTPS endpoints under **Rijksoverheid**  


### Scanning examples

```
curl --silent https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/endpoints.txt | ./nuclei -silent -id geoserver-login-panel
```

```
curl --silent https://raw.githubusercontent.com/zzzteph/DutchGovScope/refs/heads/main/storage/rijksoverheid/endpoints.txt | ./nuclei -silent -id exposure -severity critical,high
```





## Links and Acknowledgements

- [Bug Bounty Dutch Government Scope – Gist](https://gist.github.com/zzzteph/99a7bd2acde12cb4b2626fc9261bc56d)  
- [basisbeveiliging.nl](https://basisbeveiliging.nl/)  
- [overheid.nl](https://www.overheid.nl/english/dutch-government-websites)  
- [communicatierijk.nl](https://www.communicatierijk.nl/vakkennis/r/rijkswebsites/verplichte-richtlijnen/websiteregister-rijksoverheid)  
- [ncsc.nl](https://www.ncsc.nl/contact/kwetsbaarheid-melden/cvd-meldingen-formulier)  
- [shrewdeye.app](https://shrewdeye.app)  
- [NCSC Wall of Fame](https://www.ncsc.nl/contact/kwetsbaarheid-melden/wall-of-fame)  

---

To report a vulnerability or learn more, please visit:  
👉 [https://www.ncsc.nl/contact/kwetsbaarheid-melden](https://www.ncsc.nl/contact/kwetsbaarheid-melden)