"""Re-seed the KOID BENG SUN test fixture (Client + Will).

The cost-crisis cleanup wiped the KOID Client row from production. The
autonomous Step-6 loop expects this fixture by ID. Re-running this
script restores the Client + a fresh Will attached to it. After that
you can re-forward the WhatsApp test email (with vision enabled) to
populate Documents and AI Summary, OR seed inline data if you prefer
a deterministic offline test.

Usage:
    docker exec willcraft-web python /app/tests/step6/seed_koid.py
    docker exec willcraft-web python /app/tests/step6/seed_koid.py --force-recreate
"""
import sys
import json
import argparse
sys.path.insert(0, '/app')

from datetime import datetime
from app import app
from database import db, Client, Will

KOID_CLIENT_ID = '0590d69b-f171-4764-8bf6-642b20b824f6'
KOID_FULL_NAME = 'KOID BENG SUN'
KOID_NRIC = '631204-07-5743'

# Default user_id for the seeded fixture — pick the first admin so the
# tenant-scoped query (§ Multi-tenant isolation) returns this Client to
# the tester. NULL is also fine because legacy NULL-owner is treated
# as globally visible.
SEED_USER_ID = None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--force-recreate', action='store_true',
                   help='Drop existing rows and re-create from scratch.')
    args = p.parse_args()

    with app.app_context():
        c = db.session.get(Client, KOID_CLIENT_ID)
        if c and not args.force_recreate:
            print(f"OK — Client {KOID_CLIENT_ID} already exists.")
            print(f"     full_name={c.full_name!r} nric={c.nric_passport!r}")
            print(f"     created_by={c.created_by!r}")
            wills = Will.query.filter_by(client_id=KOID_CLIENT_ID).all()
            print(f"     wills attached: {len(wills)}")
            for w in wills:
                print(f"       - {w.id}  status={w.status}  title={w.title!r}")
            return 0

        if c and args.force_recreate:
            # Wipe attached wills + persons + documents first to avoid FK churn.
            from database import Person, Document
            Document.query.filter_by(client_id=KOID_CLIENT_ID).delete()
            Person.query.filter_by(client_id=KOID_CLIENT_ID).delete()
            for w in Will.query.filter_by(client_id=KOID_CLIENT_ID).all():
                db.session.delete(w)
            db.session.delete(c)
            db.session.commit()
            print(f"Wiped existing Client {KOID_CLIENT_ID} and its descendants.")

        # Pick a user_id for created_by — first admin if any
        global SEED_USER_ID
        if SEED_USER_ID is None:
            try:
                from database import User
                u = User.query.filter(User.role.in_(['admin', 'advisor'])).first()
                SEED_USER_ID = u.id if u else None
            except Exception:
                SEED_USER_ID = None

        c = Client(
            id=KOID_CLIENT_ID,
            full_name=KOID_FULL_NAME,
            nric_passport=KOID_NRIC,
            email=None,
            phone=None,
            created_by=SEED_USER_ID,
        )
        db.session.add(c)

        w = Will(
            client_id=KOID_CLIENT_ID,
            title='KOID — fixture',
            status='draft',
            step1_data=json.dumps({
                'full_name': KOID_FULL_NAME,
                'nric_passport': KOID_NRIC,
            }),
            step2_data='[]', step3_data='{}', step4_data='[]',
            step5_data='[]', step6_data='{}', step7_data='{}',
            step8_data='{}', completed_steps='[]',
            created_by=SEED_USER_ID,
        )
        db.session.add(w)
        db.session.commit()

        print(f"OK — seeded Client {KOID_CLIENT_ID}")
        print(f"     full_name={c.full_name!r} nric={c.nric_passport!r}")
        print(f"     created_by={c.created_by!r}")
        print(f"     attached Will id={w.id} status={w.status}")
        print()
        print("Next steps:")
        print(f"  1. Forward the test WhatsApp email to "
              f"koid5743@will.alantanjb.com  (kill switch must be OFF)")
        print(f"  2. Wait ~30s for AI Summary card to land")
        print(f"  3. Run reset_step6 / walk_step6 / verify_step6 as before")
    return 0


if __name__ == '__main__':
    sys.exit(main())
