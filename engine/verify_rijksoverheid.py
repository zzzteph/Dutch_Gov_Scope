"""
Three-tier verification pipeline for Dutch government (rijksoverheid) domains.

Tier 1   — raw HTTP + SSL cert:  fast, free, catches server-rendered sites
Tier 1.5 — Playwright DOM render: runs the same signal checks on the *fully rendered*
            DOM so SPAs (React, Vue, Angular …) are checked after JavaScript executes.
            Also captures the header screenshot for Tier 2 in the same browser session.
Tier 2   — Claude vision on the screenshot taken in Tier 1.5
Tier 3   — manual review queue

Usage:
    python verify_rijksoverheid.py scope/rijksoverheid.txt
    python verify_rijksoverheid.py scope/rijksoverheid.txt --output-dir ./results
    python verify_rijksoverheid.py scope/rijksoverheid.txt --no-vision   # Tier 1 + 1.5 only
    python verify_rijksoverheid.py scope/rijksoverheid.txt --concurrency 10

Outputs (in --output-dir):
    confirmed.txt       — verified Dutch government domains
    rejected.txt        — clearly NOT Dutch government
    manual_review.txt   — ambiguous, needs human check
    details.jsonl       — one JSON record per domain with tier/reason
"""

import argparse
import asyncio
import base64
import json
import os
import socket
import ssl
import sys
from pathlib import Path

# Load ANTHROPIC_API_KEY from a .env file in the project root if not already set.
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Windows console may use a narrow codepage that can't encode OS error messages.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Signal definitions  (shared between Tier 1 and Tier 1.5)
# ---------------------------------------------------------------------------

# Any single hit in the page body → CONFIRMED
STRONG_BODY_SIGNALS = [
    # Dutch government meta tag names (appear as HTML attribute values)
    "rijksoverheid.org",
    "overheid:authority",
    "overheid.koninkrijksdeel",
    "rijksoverheid.informatietype",
    "rijksoverheid.organisatie",
    # Standard disclaimer text
    "officiële website van de nederlandse overheid",
    "official website of the dutch government",
    # Accessibility statement — legally required for all Dutch gov sites
    "toegankelijkheidsverklaring.nl",
    # Government analytics infra (data-host="statistiek.rijksoverheid.nl" etc.)
    "statistiek.rijksoverheid.nl",
    "piwik.rijksoverheid.nl",
    "webstats.rijksoverheid.nl",
    "cdn.rijksoverheid.nl",
    "api.rijksoverheid.nl",
    "rijkshuisstijl.nl",
]

# Any single hit → CONFIRMED
STRONG_LINK_SIGNALS = [
    "rijksoverheid.nl",
    "government.nl",
    "overheid.nl",
]

# Need 2+ hits → CONFIRMED
MEDIUM_BODY_SIGNALS = [
    "rijkshuisstijl",
    "nldesignsystem",
    "logius.nl",
    "ssc-ict.nl",
    "p-direkt.nl",
    "digid.nl",
    "e-herkenning",
    "postbus 51",
]

# SSL cert O= field → CONFIRMED
SSL_GOV_KEYWORDS = [
    "ministerie",
    "rijkswaterstaat",
    "rijksoverheid",
    "belastingdienst",
    "staat der nederlanden",
    "rijksdienst",
    "politie",
    "defensie",
]


# ---------------------------------------------------------------------------
# Signal matching (reused by both Tier 1 and Tier 1.5)
# ---------------------------------------------------------------------------

def match_signals(body: str) -> tuple[str, str] | None:
    """
    Run all body signal checks. Returns (verdict, reason) if a signal fires,
    or None if nothing matched.
    """
    body = body.lower()

    for signal in STRONG_BODY_SIGNALS:
        if signal in body:
            return "confirmed", f"body signal: '{signal}'"

    for signal in STRONG_LINK_SIGNALS:
        if signal in body:
            return "confirmed", f"link signal: '{signal}'"

    medium_hits = [s for s in MEDIUM_BODY_SIGNALS if s in body]
    if len(medium_hits) >= 2:
        return "confirmed", f"medium signals ({len(medium_hits)}): {medium_hits}"

    return None


# ---------------------------------------------------------------------------
# Tier 1 — raw HTTP + SSL cert
# ---------------------------------------------------------------------------

def get_ssl_org(domain: str) -> str:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname=domain) as tls:
                subject = dict(x[0] for x in tls.getpeercert().get("subject", []))
                return subject.get("organizationName", "").lower()
    except Exception:
        return ""


def tier1_check(domain: str) -> tuple[str, str]:
    try:
        response = requests.get(
            f"https://{domain}",
            timeout=8,
            allow_redirects=True,
            verify=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DutchGovScopeVerifier/1.0)"},
        )
    except requests.exceptions.RequestException as exc:
        return "uncertain", f"connection error: {exc}"

    if not response.ok:
        return "uncertain", f"HTTP {response.status_code}"

    hit = match_signals(response.text)
    if hit:
        return hit

    ssl_org = get_ssl_org(domain)
    for kw in SSL_GOV_KEYWORDS:
        if kw in ssl_org:
            return "confirmed", f"SSL cert org: '{ssl_org}'"

    return "uncertain", "no strong signals found in raw HTTP response"


# ---------------------------------------------------------------------------
# Tier 1.5 — Playwright DOM render + signal check + screenshot
# Returns (verdict, reason, screenshot_bytes | None)
# ---------------------------------------------------------------------------

async def playwright_check(domain: str, pw) -> tuple[str, str, bytes | None]:
    browser = None
    try:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1024, "height": 400})

        await page.goto(f"https://{domain}", timeout=15000, wait_until="domcontentloaded")

        # Give SPAs up to 3 s to finish their initial JS render
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        # Signal check on the fully rendered DOM
        rendered_html = await page.content()
        hit = match_signals(rendered_html)

        # Screenshot of the header band (small JPEG)
        try:
            screenshot = await page.screenshot(
                clip={"x": 0, "y": 0, "width": 1024, "height": 380},
                type="jpeg",
                quality=75,
            )
        except Exception:
            screenshot = None

        if hit:
            return hit[0], f"[rendered DOM] {hit[1]}", screenshot

        return "uncertain", "no strong signals in rendered DOM", screenshot

    except Exception as exc:
        return "uncertain", f"playwright error: {exc}", None
    finally:
        if browser:
            await browser.close()


# ---------------------------------------------------------------------------
# Tier 2 — Claude vision
# ---------------------------------------------------------------------------

VISION_PROMPT = """\
You are verifying whether a website belongs to the Dutch national government (Rijksoverheid).

Look for these visual indicators IN THE HEADER OR FOOTER OF THE PAGE:

STRONG indicators (any one is enough):
1. The standard Rijksoverheid header: dark navy/blue bar with the white Dutch coat of arms
   (lion holding arrows and sword) followed by the word "Rijksoverheid" in white text,
   often with a pink/magenta horizontal stripe beneath it.
2. "Ministerie van ..." logo or name (any Dutch ministry) as the SITE'S OWN identity.
3. Agency logos used as the SITE'S OWN branding: Politie, Belastingdienst, Rijkswaterstaat,
   Defensie, DUO, RIVM, CBS, RVO, RWS, IND, etc.
4. Text visible in the page: "Dit is een officiële website van de Nederlandse overheid".
5. Dutch coat of arms (rijkswapen) used as part of the site's own header/logo.
6. The NL-flag ribbon (horizontal red-white-blue or orange stripe) in a government context.

IMPORTANT — do NOT confirm based on:
- Partner/sponsor logos shown as "working together with" tiles in footers or sidebars.
- The EU flag, UN logos, or international partner badges.
- Generic images of the Netherlands or Dutch imagery without government branding.

Respond with EXACTLY one of:
  CONFIRMED: <brief reason>
  REJECTED: <brief reason>
  UNCERTAIN: <brief reason>

Only say CONFIRMED if you clearly see Dutch government visual identity as the SITE'S OWN branding.
When in doubt, say UNCERTAIN.\
"""


def vision_classify(screenshot_bytes: bytes, client) -> tuple[str, str]:
    image_b64 = base64.standard_b64encode(screenshot_bytes).decode()
    try:
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
                    },
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
        )
        answer = msg.content[0].text.strip()
        if answer.startswith("CONFIRMED"):
            return "confirmed", answer
        if answer.startswith("REJECTED"):
            return "rejected", answer
        return "uncertain", answer
    except Exception as exc:
        return "uncertain", f"vision API error: {exc}"


# ---------------------------------------------------------------------------
# Per-domain pipeline
# ---------------------------------------------------------------------------

async def process_domain(domain, semaphore, pw, anthropic_client, use_browser) -> dict:
    async with semaphore:

        # Tier 1 — raw HTTP + SSL (no browser)
        verdict, reason = await asyncio.to_thread(tier1_check, domain)
        if verdict != "uncertain":
            print(f"  [T1 {verdict.upper():9}] {domain}  ({reason})", flush=True)
            return {"domain": domain, "status": verdict, "tier": 1, "reason": reason}

        if not use_browser:
            print(f"  [REVIEW    ] {domain}  ({reason})", flush=True)
            return {"domain": domain, "status": "manual_review", "tier": 3, "reason": reason}

        # Tier 1.5 — Playwright: rendered DOM check + screenshot
        verdict, reason, screenshot = await playwright_check(domain, pw)
        if verdict != "uncertain":
            print(f"  [T1.5 {verdict.upper():6}] {domain}  ({reason})", flush=True)
            return {"domain": domain, "status": verdict, "tier": 1.5, "reason": reason}

        # Tier 2 — Claude vision on the screenshot captured above
        if screenshot and anthropic_client:
            verdict, reason = await asyncio.to_thread(
                vision_classify, screenshot, anthropic_client
            )
            if verdict != "uncertain":
                print(f"  [T2 {verdict.upper():9}] {domain}  (vision: {reason})", flush=True)
                return {"domain": domain, "status": verdict, "tier": 2, "reason": reason}

        # Tier 3 — manual review
        print(f"  [REVIEW    ] {domain}  ({reason})", flush=True)
        return {"domain": domain, "status": "manual_review", "tier": 3, "reason": reason}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run(args):
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found")
        sys.exit(1)

    domains = [l.strip() for l in input_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"Loaded {len(domains)} domains from {input_path}")

    use_browser = PLAYWRIGHT_AVAILABLE and not args.no_vision
    use_vision  = use_browser and ANTHROPIC_AVAILABLE

    if not args.no_vision:
        if not PLAYWRIGHT_AVAILABLE:
            print("Warning: playwright not installed — install with: pip install playwright && playwright install chromium")
        elif not ANTHROPIC_AVAILABLE:
            print("Warning: anthropic not installed — vision disabled (pip install anthropic)")

    mode = "Tier 1 only"
    if use_browser and not use_vision:
        mode = "Tier 1 + 1.5 (rendered DOM, no vision)"
    elif use_vision:
        mode = "Tier 1 + 1.5 (rendered DOM) + Tier 2 (vision)"

    print(f"Mode: {mode}")
    print(f"Concurrency: {args.concurrency}\n")

    anthropic_client = None
    if use_vision:
        if os.environ.get("ANTHROPIC_API_KEY"):
            anthropic_client = anthropic.Anthropic()
        else:
            print("Warning: anthropic installed but ANTHROPIC_API_KEY not set — vision disabled, falling back to Tier 1 + 1.5")
            use_vision = False
    semaphore = asyncio.Semaphore(args.concurrency)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if use_browser:
        async with async_playwright() as pw:
            tasks = [process_domain(d, semaphore, pw, anthropic_client, True) for d in domains]
            results = await asyncio.gather(*tasks)
    else:
        tasks = [process_domain(d, semaphore, None, None, False) for d in domains]
        results = await asyncio.gather(*tasks)

    confirmed     = [r for r in results if r["status"] == "confirmed"]
    rejected      = [r for r in results if r["status"] == "rejected"]
    manual_review = [r for r in results if r["status"] == "manual_review"]

    (output_dir / "confirmed.txt").write_text("\n".join(r["domain"] for r in confirmed), encoding="utf-8")
    (output_dir / "rejected.txt").write_text("\n".join(r["domain"] for r in rejected), encoding="utf-8")
    (output_dir / "manual_review.txt").write_text("\n".join(r["domain"] for r in manual_review), encoding="utf-8")
    (output_dir / "details.jsonl").write_text("\n".join(json.dumps(r) for r in results), encoding="utf-8")

    print(f"\n{'─'*50}")
    print(f"  Confirmed:     {len(confirmed):>4}")
    print(f"  Rejected:      {len(rejected):>4}")
    print(f"  Manual review: {len(manual_review):>4}")
    print(f"{'─'*50}")
    print(f"Results written to {output_dir}/")


def main():
    parser = argparse.ArgumentParser(description="Verify Dutch government ownership of domains")
    parser.add_argument("input", help="Input file — one domain per line")
    parser.add_argument("--output-dir", default="./verification_results",
                        help="Directory for output files (default: ./verification_results)")
    parser.add_argument("--no-vision", action="store_true",
                        help="Skip Tier 1.5 browser render and Tier 2 vision; Tier 1 (raw HTTP) only")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Max concurrent domain checks (default: 5)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
