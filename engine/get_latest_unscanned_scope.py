from db import Scope, session
from datetime import datetime, timezone
from sqlalchemy import and_
def utc_now():
    return datetime.now(timezone.utc)

def get_latest_unscanned_or_oldest_scanned():
 
    latest_unscanned = (
        session.query(Scope)
        .filter(and_(Scope.type == "domains", Scope.scanned == False))
        .order_by(Scope.created_at.desc())
        .first()
    )

    if not latest_unscanned:
        latest_unscanned = (
            session.query(Scope)
            .filter(Scope.type == "domains")
            .order_by(Scope.updated_at.asc())
            .first()
    )


    if latest_unscanned:
        print(f"LATEST_SCOPE_ID={latest_unscanned.id}")
        print(f"LATEST_SCOPE_NAME={latest_unscanned.name}")
        print(f"LATEST_SCOPE_TYPE ={latest_unscanned.type}")
        latest_unscanned.updated_at = utc_now()
        latest_unscanned.scanned = True
        session.commit()
        return latest_unscanned
    else:
        print("LATEST_SCOPE_ID=None")
        print("LATEST_SCOPE_NAME=None")
        print("LATEST_SCOPE_TYPE=None")
        return None

if __name__ == "__main__":
    get_latest_unscanned_or_oldest_scanned()