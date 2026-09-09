#!/usr/bin/env python3
"""Add one family and one parent from the command line.

    python scripts/seed.py --child Ravi --child-phone +9198xxxxxxx --parent "Sushila Devi" \
        --parent-phone +9197xxxxxxx --language hi-IN --time 08:30 --consent
"""
import argparse
import json

from sahara.db import init_db, session
from sahara.models import Family, Parent, utcnow

ap = argparse.ArgumentParser()
ap.add_argument("--child", required=True); ap.add_argument("--child-phone", required=True)
ap.add_argument("--child-language", default="en")
ap.add_argument("--parent", required=True); ap.add_argument("--parent-phone", required=True)
ap.add_argument("--language", default="hi-IN"); ap.add_argument("--time", default="08:30")
ap.add_argument("--meds", default="", help='JSON list, e.g. [{"name":"Amlodipine","when":"morning"}]')
ap.add_argument("--notes", default=""); ap.add_argument("--consent", action="store_true")
a = ap.parse_args()
init_db()
with session() as s:
    f = Family(child_name=a.child, child_phone=a.child_phone, child_language=a.child_language)
    s.add(f); s.commit(); s.refresh(f)
    p = Parent(family_id=f.id, name=a.parent, phone=a.parent_phone, language=a.language, call_time=a.time,
               medications=json.dumps(json.loads(a.meds) if a.meds else []), notes=a.notes,
               consent=a.consent, consent_at=utcnow() if a.consent else None)
    s.add(p); s.commit(); s.refresh(p)
    print(f"family {f.id}: {f.child_name} | parent {p.id}: {p.name} {p.phone} {p.language} at {p.call_time} consent={p.consent}")
