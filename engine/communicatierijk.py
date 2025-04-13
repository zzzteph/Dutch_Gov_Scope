import requests
import re
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse
import tldextract
import urllib3
import sys

def get_root_domain(url):
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}"



if len(sys.argv) != 2:
    print("Usage: python communicatierijk.py file_to_update")
    sys.exit(1)

file_path = sys.argv[1]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
headers_to_check = ["RIJKSOVERHEID.Org", "overheid:authority","OVERHEID.Koninkrijksdeel","RIJKSOVERHEID.Informatietype","RIJKSOVERHEID.Organisatie"]

ods_path = Path("/tmp/result.ods")
csv_path = Path("/tmp/result.csv")

base_url = "https://www.communicatierijk.nl"
page_url = base_url + "/vakkennis/r/rijkswebsites/verplichte-richtlijnen/websiteregister-rijksoverheid"

response = requests.get(page_url, verify=False)
response.raise_for_status()

match = re.search(r'href="([^"]+\.ods)"', response.text)
if not match:
    raise Exception("ODS link not found")

ods_url = base_url + match.group(1)
ods_data = requests.get(ods_url, verify=False)
ods_data.raise_for_status()
ods_path.write_bytes(ods_data.content)
df = pd.read_excel(ods_path, engine="odf")
df.to_csv(csv_path, index=False)
df_csv = pd.read_csv(csv_path)
entries = set()
if not df_csv.empty:
    first_column_name = df_csv.columns[0]
    for value in df_csv[first_column_name]:
        if isinstance(value, str):
            parsed = urlparse(value)
            domain = parsed.hostname
            if domain:
                ext = tldextract.extract(domain)
                root_domain = f"{ext.domain}.{ext.suffix}"
                entries.add(root_domain)
else:
    print("CSV is empty.")

result_domains=set()

for domain in sorted(entries):
    try:
        response = requests.get(f"https://{domain}", timeout=10, allow_redirects=True, verify=False)
        
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