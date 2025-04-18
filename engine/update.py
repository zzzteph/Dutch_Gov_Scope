import os
import ipaddress
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from db import Scope, Base
from datetime import datetime, timezone

DATABASE_URL = "sqlite:///scope_scan.db"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

SCOPE_FOLDER = "scope"

def utc_now():
    return datetime.now(timezone.utc)

def determine_type(entry):
    """Determines if an entry is an IP range or a domain."""
    try:
        ipaddress.ip_network(entry, strict=False)
        return "ip_range"
    except ValueError:
        return "domains"

def sync_scope():
    scopes = session.query(Scope).order_by(Scope.updated_at.asc()).all()
    
    for scope in scopes:
        found=False
        for file_name in os.listdir(SCOPE_FOLDER):
            if file_name.endswith(".txt"):
                tag = os.path.splitext(file_name)[0]
                file_path = os.path.join(SCOPE_FOLDER, file_name)
                with open(file_path, "r", encoding="utf-8") as file:
                    for line in file:
                        entry = line.strip()
                        if scope.tag==tag and scope.name==entry:
                            found=True
        if not found:
            session.delete(scope)
            session.commit()
                        


def process_scope_files():
    """Scans the scope folder, parses .txt files, and adds new scope entries."""
    if not os.path.exists(SCOPE_FOLDER):
        print(f"Folder '{SCOPE_FOLDER}' not found.")
        return

    for file_name in os.listdir(SCOPE_FOLDER):
        if file_name.endswith(".txt"):
            tag = os.path.splitext(file_name)[0]
            file_path = os.path.join(SCOPE_FOLDER, file_name)
            
            with open(file_path, "r", encoding="utf-8") as file:
                for line in file:
                    entry = line.strip()
                    if entry:
                        add_scope_entry(entry, tag)

def add_scope_entry(name, tag):
    """Adds a new scope entry if it does not exist."""
    entry_type = determine_type(name)
    exists = session.query(Scope).filter_by(name=name).first()
    
    if not exists:
        new_scope = Scope(
            name=name,
            type=entry_type,
            tag=tag,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(new_scope)
        print(f"Added: {name} ({entry_type}) with tag '{tag}'")
    else:
        print(f"Skipped (exists): {name}")




def main():
    sync_scope()
    process_scope_files()
    session.commit()
    session.close()
    print("Scope update complete.")

if __name__ == "__main__":
    main()