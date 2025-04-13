import requests
import csv
from io import StringIO
import tldextract
from urllib.parse import urlparse
import sys
import os

requests.packages.urllib3.disable_warnings()

if len(sys.argv) != 2:
    print("Usage: python basisbeveiliging.py file_to_update")
    sys.exit(1)

file_path = sys.argv[1]


gov_links = [
    'https://basisbeveiliging.nl/data/export/urls_only/NL/government/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_employment/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_defense/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_general_affairs/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_agriculture/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_health/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_infrastructure/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_finance/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_economy/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_interior_relations/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_justice/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_foreign_affairs/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_education/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_health/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_agriculture/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_education/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_justice/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_employment/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_infrastructure/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_finance/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_economy/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_defense/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_foreign_affairs/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_interior_relations/csv/',
    'https://basisbeveiliging.nl/data/export/urls_only/NL/central_government_general_affairs/csv/',
    ]

def get_root_domain(url):
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}"

entries = set()

for link in gov_links:
    try:
        response = requests.get(link, timeout=10)
        response.raise_for_status()
        print(f"Fetched: {link}")
        
        csv_reader = csv.reader(StringIO(response.text))
        for row in csv_reader:
            if len(row) > 1:
                domain = row[1].strip()
                if domain.endswith(".nl"):
                    entries.add(get_root_domain(domain))
    except Exception as e:
        print(f"Failed to fetch {link}: {e}")

headers_to_check = ["RIJKSOVERHEID.Org", "overheid:authority","OVERHEID.Koninkrijksdeel","RIJKSOVERHEID.Informatietype","RIJKSOVERHEID.Organisatie"]

result_domains=set()





for domain in sorted(entries):
    
    print(f"{domain} explored")        
    try:
        response = requests.get(f"https://{domain}", timeout=5, allow_redirects=True, verify=False)
        if response.ok:
            body = response.text.lower()
            if any(keyword.lower() in body.lower() for keyword in headers_to_check):
                final_url = response.url
                parsed = urlparse(final_url)
                final_domain = parsed.hostname
                if final_domain:
                    result_domains.add(get_root_domain(final_domain.removeprefix("www.")))
    except requests.exceptions.RequestException:
        pass

with open(file_path, "a") as f:
    for domain in result_domains:
        f.write(domain + "\n")