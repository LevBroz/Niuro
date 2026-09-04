import sys
from collections import defaultdict

import state
from config import WAREHOUSE_PATH
from identity import (
    email_is_valid,
    is_test_account,
    national_id_is_usable,
    normalize_email,
    normalize_national_id,
    normalize_phone,
    phone_is_valid,
)

SURVIVING_STATUSES = ("funded", "paid_off")

DDL = """
CREATE SCHEMA IF NOT EXISTS dq;

CREATE OR REPLACE TABLE dq.customer_resolution (
    customer_id       INTEGER,
    survivor_id       INTEGER,
    group_key         VARCHAR,
    action            VARCHAR,
    reason            VARCHAR,
    protected         BOOLEAN
);

CREATE OR REPLACE TABLE dq.manual_review (
    group_key         VARCHAR,
    customer_ids      VARCHAR,
    reason            VARCHAR
);

CREATE OR REPLACE TABLE dq.contact_quality (
    customer_id       INTEGER,
    email_status      VARCHAR,
    phone_status      VARCHAR
);
"""


def load_customers(wh):
    return wh.execute(
        """
        SELECT c.customer_id, c.first_name, c.last_name, c.email, c.phone,
               c.date_of_birth, c.national_id, c.created_at,
               COALESCE(a.protected, FALSE) AS protected,
               COALESCE(t.txn_count, 0)     AS txn_count,
               COALESCE(a.advance_count, 0) AS advance_count
          FROM main.customers c
          LEFT JOIN (
                SELECT customer_id,
                       BOOL_OR(status IN ('funded', 'paid_off')) AS protected,
                       COUNT(*) AS advance_count
                  FROM main.advances
                 WHERE NOT is_deleted
                 GROUP BY customer_id
          ) a ON a.customer_id = c.customer_id
          LEFT JOIN (
                SELECT customer_id, COUNT(*) AS txn_count
                  FROM main.transactions
                 GROUP BY customer_id
          ) t ON t.customer_id = c.customer_id
         WHERE NOT c.is_deleted
        """
    ).fetchall()


def classify_contacts(wh, rows):
    payload = []
    for r in rows:
        email, phone = r[3], r[4]
        email_status = (
            "missing" if email is None
            else "valid" if email_is_valid(email)
            else "malformed"
        )
        phone_status = (
            "missing" if phone is None
            else "valid" if phone_is_valid(phone)
            else "malformed"
        )
        payload.append((r[0], email_status, phone_status))
    wh.executemany(
        "INSERT INTO dq.contact_quality VALUES (?, ?, ?)", payload
    )
    return payload


def group_key(row):
    national_id = normalize_national_id(row[6])
    if national_id and national_id_is_usable(row[6]):
        return f"nid:{national_id}"

    last_name = (row[2] or "").strip().lower()
    dob = row[5]
    email = normalize_email(row[3])
    if last_name and dob and email and email_is_valid(email):
        return f"name_dob_email:{last_name}|{dob}|{email}"

    return None


def pick_survivor(members):
    protected = [m for m in members if m[8]]
    pool = protected if protected else members
    return sorted(
        pool,
        key=lambda m: (-(m[9] + m[10]), m[7], m[0]),
    )[0]


def resolve(wh, rows):
    groups = defaultdict(list)
    singles = []
    for row in rows:
        key = group_key(row)
        if key is None:
            singles.append(row)
        else:
            groups[key].append(row)

    resolutions = []
    reviews = []

    for row in singles:
        resolutions.append(
            (row[0], row[0], None, "keep", "no_reliable_grouping_key", bool(row[8]))
        )

    for key, members in groups.items():
        if len(members) == 1:
            m = members[0]
            resolutions.append((m[0], m[0], key, "keep", "unique_in_group", bool(m[8])))
            continue

        protected = [m for m in members if m[8]]
        if len(protected) > 1:
            ids = ",".join(str(m[0]) for m in members)
            reviews.append((key, ids, "multiple_protected_customers_in_group"))
            for m in members:
                resolutions.append(
                    (m[0], m[0], key, "hold", "multiple_protected_customers_in_group", True)
                )
            continue

        survivor = pick_survivor(members)
        for m in members:
            if m[0] == survivor[0]:
                resolutions.append(
                    (m[0], m[0], key, "survivor", "selected_by_activity_then_age", bool(m[8]))
                )
            else:
                resolutions.append(
                    (m[0], survivor[0], key, "merge", "duplicate_of_survivor", bool(m[8]))
                )

    wh.executemany(
        "INSERT INTO dq.customer_resolution VALUES (?, ?, ?, ?, ?, ?)", resolutions
    )
    if reviews:
        wh.executemany("INSERT INTO dq.manual_review VALUES (?, ?, ?)", reviews)
    return resolutions, reviews


def flag_test_accounts(wh, rows):
    flagged = []
    for r in rows:
        has_activity = (r[9] + r[10]) > 0
        is_test, reason = is_test_account(r[3], r[6], r[4], has_activity)
        if is_test:
            flagged.append((r[0], reason))

    wh.execute(
        "CREATE OR REPLACE TABLE dq.excluded_accounts "
        "(customer_id INTEGER, reason VARCHAR)"
    )
    if flagged:
        wh.executemany("INSERT INTO dq.excluded_accounts VALUES (?, ?)", flagged)
    return flagged


def build_golden_views(wh):
    wh.execute(
        """
        CREATE OR REPLACE VIEW dq.customer_golden AS
        SELECT c.*
          FROM main.customers c
          JOIN dq.customer_resolution r ON r.customer_id = c.customer_id
         WHERE r.action IN ('keep', 'survivor', 'hold')
           AND c.customer_id NOT IN (SELECT customer_id FROM dq.excluded_accounts)
           AND NOT c.is_deleted
        """
    )

    # cards follow the surviving customer; the original owner stays on the row
    # so a wrong merge can be traced and reversed without touching main.cards
    wh.execute(
        """
        CREATE OR REPLACE VIEW dq.card_ownership AS
        SELECT k.card_id,
               r.survivor_id       AS customer_id,
               k.customer_id       AS source_customer_id,
               k.last_four,
               k.brand,
               k.status,
               k.expires_on,
               r.action            AS resolution_action
          FROM main.cards k
          JOIN dq.customer_resolution r ON r.customer_id = k.customer_id
         WHERE NOT k.is_deleted
        """
    )


def report(wh, resolutions, reviews, flagged, contacts):
    actions = defaultdict(int)
    for r in resolutions:
        actions[r[3]] += 1

    print("resolution")
    for action in ("keep", "survivor", "merge", "hold"):
        print(f"  {action:<10}{actions[action]:>8,}")

    print(f"\nexcluded test accounts   {len(flagged):>6,}")
    for cid, reason in flagged[:10]:
        print(f"  customer {cid:<6} {reason}")

    print(f"\nmanual review groups     {len(reviews):>6,}")
    for key, ids, reason in reviews:
        print(f"  {key}  ids={ids}  {reason}")

    email_bad = sum(1 for c in contacts if c[1] == "malformed")
    email_missing = sum(1 for c in contacts if c[1] == "missing")
    phone_bad = sum(1 for c in contacts if c[2] == "malformed")
    phone_missing = sum(1 for c in contacts if c[2] == "missing")
    print("\ncontact quality")
    print(f"  email malformed {email_bad:>6,}   missing {email_missing:>6,}")
    print(f"  phone malformed {phone_bad:>6,}   missing {phone_missing:>6,}")

    moved = wh.execute(
        """
        SELECT COUNT(*) FROM dq.card_ownership
         WHERE customer_id <> source_customer_id
        """
    ).fetchone()[0]
    orphans = wh.execute(
        """
        SELECT COUNT(*) FROM dq.card_ownership o
         WHERE NOT EXISTS (
            SELECT 1 FROM dq.customer_golden g WHERE g.customer_id = o.customer_id
         )
        """
    ).fetchone()[0]
    print(f"\ncards reassigned to survivor {moved:>6,}")
    print(f"cards with no surviving owner {orphans:>5,}")


def main():
    wh = state.connect(WAREHOUSE_PATH)
    wh.execute(DDL)

    rows = load_customers(wh)
    contacts = classify_contacts(wh, rows)
    flagged = flag_test_accounts(wh, rows)
    resolutions, reviews = resolve(wh, rows)
    build_golden_views(wh)
    report(wh, resolutions, reviews, flagged, contacts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
