import os
import requests

GIST_ID = os.environ["GIST_ID"]
GIST_TOKEN = os.environ["GIST_TOKEN"]

FILES = {
    "scope.txt": "scope/rijksoverheid.txt",
    "README.md": "README.md",
}


def update_gist():
    url = f"https://api.github.com/gists/{GIST_ID}"
    headers = {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    gist = requests.get(url, headers=headers).json()
    current = {name: gist["files"].get(name, {}).get("content", "") for name in FILES}

    updates = {}
    for target, source in FILES.items():
        if not os.path.exists(source):
            print(f"File not found: {source}")
            continue
        with open(source, "r", encoding="utf-8") as f:
            content = f.read()
        if content.strip() != current[target].strip():
            updates[target] = {"content": content}

    if not updates:
        print("No changes — gist is up to date.")
        return

    response = requests.patch(url, json={"files": updates}, headers=headers)
    if response.status_code == 200:
        print(f"Gist updated: {', '.join(updates.keys())}")
    else:
        print(f"Failed to update gist: {response.status_code}")
        print(response.json())


if __name__ == "__main__":
    update_gist()
