import random
import sys
from datetime import date, datetime, timedelta

import pyodbc
from faker import Faker

from config import SEED, mssql_dsn

fake = Faker("en_US")
Faker.seed(SEED)
random.seed(SEED)

BASE = datetime(2024, 1, 1)

N_CLEAN_CUSTOMERS = 260
N_TRANSACTIONS = 40000


def connect(database):
    conn = pyodbc.connect(mssql_dsn(database), autocommit=True)
    return conn


def run_schema(cur):
    with open("sql/source/001_schema.sql", "r", encoding="utf-8") as fh:
        script = fh.read()
    for batch in script.split("\nGO"):
        stmt = batch.strip()
        if stmt:
            cur.execute(stmt)


def ts(days_ago, hour=9):
    return BASE - timedelta(days=days_ago) + timedelta(hours=hour)


def national_id_variants(base):
    return [
        base,
        f" {base} ",
        base.replace("-", ""),
        f"{base}\u200b",
        base.lower(),
    ]


def make_clean_customer(i):
    first = fake.first_name()
    last = fake.last_name()
    domain = random.choice(["gmail.com", "outlook.com", "yahoo.com", "proton.me"])
    email = f"{first.lower()}.{last.lower()}{random.randint(1, 99)}@{domain}"
    phone = f"+1{random.randint(2000000000, 9899999999)}"
    dob = fake.date_of_birth(minimum_age=21, maximum_age=70)
    nid = f"{random.randint(100, 899)}-{random.randint(10, 99)}-{random.randint(1000, 9999)}"
    created = ts(random.randint(200, 700))
    return {
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": phone,
        "date_of_birth": dob,
        "national_id": nid,
        "address_line": fake.street_address(),
        "city": fake.city(),
        "created_at": created,
        "updated_at": created,
    }


def duplicate_group(first, last, dob, nid, variants, city):
    rows = []
    domains = ["gmail.com", "outlook.com", "yahoo.com"]
    for idx, nid_value in enumerate(variants):
        created = ts(random.randint(120, 640), hour=8 + idx)
        sep = random.choice([".", "_", ""])
        email = f"{first.lower()}{sep}{last.lower()}{random.choice(['', str(random.randint(1,99))])}@{random.choice(domains)}"
        phone_digits = random.randint(2000000000, 9899999999)
        phone_format = random.choice(
            [
                f"+1{phone_digits}",
                f"({str(phone_digits)[:3]}) {str(phone_digits)[3:6]}-{str(phone_digits)[6:]}",
                f"{str(phone_digits)[:3]}-{str(phone_digits)[3:6]}-{str(phone_digits)[6:]}",
            ]
        )
        rows.append(
            {
                "first_name": first if idx == 0 else random.choice([first, first.upper(), first[0] + "."]),
                "last_name": last,
                "email": email,
                "phone": phone_format,
                "date_of_birth": dob,
                "national_id": nid_value,
                "address_line": fake.street_address(),
                "city": city,
                "created_at": created,
                "updated_at": created,
            }
        )
    return rows


def build_customers():
    rows = []
    tags = []

    for i in range(N_CLEAN_CUSTOMERS):
        rows.append(make_clean_customer(i))
        tags.append("clean")

    # group A: same person, three records, one of them will get a funded advance
    nid_a = "412-88-1907"
    for r in duplicate_group("Marcus", "Delgado", date(1986, 3, 14), nid_a,
                             national_id_variants(nid_a)[:3], "Tampa"):
        rows.append(r)
        tags.append("dup_funded")

    # group B: same person, two records, neither funded
    nid_b = "233-51-7742"
    for r in duplicate_group("Priya", "Raman", date(1991, 11, 2), nid_b,
                             national_id_variants(nid_b)[1:3], "Austin"):
        rows.append(r)
        tags.append("dup_plain")

    # group C: two records that both end up funded -> must not be auto-merged
    nid_c = "500-19-3388"
    for r in duplicate_group("Alice", "Nakamura", date(1979, 7, 21), nid_c,
                             national_id_variants(nid_c)[:2], "Denver"):
        rows.append(r)
        tags.append("dup_both_funded")

    # group D: two people who look alike on weak signals only.
    # shared household phone and address, different national_id and dob.
    shared_phone = "+15125550188"
    shared_address = "88 Cypress Ln"
    for first, dob, nid in [
        ("Robert", date(1963, 5, 9), "701-22-9911"),
        ("Robert", date(1992, 8, 30), "884-40-2277"),
    ]:
        created = ts(random.randint(150, 500))
        rows.append(
            {
                "first_name": first,
                "last_name": "Ferreira",
                "email": f"r.ferreira{random.randint(1,80)}@gmail.com",
                "phone": shared_phone,
                "date_of_birth": dob,
                "national_id": nid,
                "address_line": shared_address,
                "city": "Austin",
                "created_at": created,
                "updated_at": created,
            }
        )
        tags.append("same_household_not_same_person")

    return rows, tags


def build_test_and_trap_customers():
    rows = []
    tags = []

    # genuine internal test accounts
    for i in range(6):
        created = ts(random.randint(30, 400))
        rows.append(
            {
                "first_name": "QA",
                "last_name": f"Run{i}",
                "email": f"test{i}@fundo.com",
                "phone": "+15550000000",
                "date_of_birth": date(1990, 1, 1),
                "national_id": "000-00-0000",
                "address_line": "1 Test Plaza",
                "city": "Testville",
                "created_at": created,
                "updated_at": created,
            }
        )
        tags.append("test_account")

    # real people the naive LIKE '%test%' filter would wrongly delete
    traps = [
        ("Dorothy", "Testerman", "dorothy.testerman@gmail.com", "+13055551234", date(1974, 2, 18)),
        ("Ana", "Protestante", "ana.protestante@yahoo.com", "+17865559090", date(1988, 6, 6)),
        ("Neil", "Attest", "neil.attest@outlook.com", "+12125553311", date(1995, 9, 12)),
    ]
    for first, last, email, phone, dob in traps:
        created = ts(random.randint(60, 500))
        rows.append(
            {
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": phone,
                "date_of_birth": dob,
                "national_id": f"{random.randint(100,899)}-{random.randint(10,99)}-{random.randint(1000,9999)}",
                "address_line": fake.street_address(),
                "city": fake.city(),
                "created_at": created,
                "updated_at": created,
            }
        )
        tags.append("real_person_test_lookalike")

    # staff running genuine transactions from a company address
    staff = [
        ("Helena", "Cruz", "helena.cruz@fundo.com", date(1983, 4, 25)),
        ("Samuel", "Okoro", "samuel.okoro@fundo.com", date(1979, 12, 3)),
    ]
    for first, last, email, dob in staff:
        created = ts(random.randint(80, 520))
        rows.append(
            {
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": f"+1{random.randint(2000000000, 9899999999)}",
                "date_of_birth": dob,
                "national_id": f"{random.randint(100,899)}-{random.randint(10,99)}-{random.randint(1000,9999)}",
                "address_line": "500 Brickell Ave",
                "city": "Miami",
                "created_at": created,
                "updated_at": created,
            }
        )
        tags.append("staff_real_activity")

    return rows, tags


def apply_malformed_contacts(rows, tags):
    bad_emails = ["not-an-email", "missing@", "@nodomain.com", "two@@at.com", "spaced out@mail.com"]
    bad_phones = ["", "12", "n/a", "+1-555-CALL-NOW", "0000000000", "555 1234"]

    eligible = [i for i, t in enumerate(tags) if t == "clean"]
    random.shuffle(eligible)

    for i in eligible[:18]:
        rows[i]["email"] = random.choice(bad_emails)
    for i in eligible[18:40]:
        rows[i]["phone"] = random.choice(bad_phones)
    for i in eligible[40:48]:
        rows[i]["email"] = None
        rows[i]["phone"] = None
    return rows


def insert_customers(cur, rows):
    cur.fast_executemany = True
    cur.executemany(
        """
        INSERT INTO dbo.customers
            (first_name, last_name, email, phone, date_of_birth,
             national_id, address_line, city, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["first_name"], r["last_name"], r["email"], r["phone"],
                r["date_of_birth"], r["national_id"], r["address_line"],
                r["city"], r["created_at"], r["updated_at"],
            )
            for r in rows
        ],
    )


def build_advances(cur, tags):
    cur.execute("SELECT customer_id FROM dbo.customers ORDER BY customer_id")
    ids = [r[0] for r in cur.fetchall()]
    by_tag = {}
    for cid, tag in zip(ids, tags):
        by_tag.setdefault(tag, []).append(cid)

    advances = []

    funded_target = by_tag["dup_funded"][1]
    advances.append((funded_target, 4500.00, "paid_off", date(2023, 2, 10), date(2023, 8, 4)))

    for cid in by_tag["dup_both_funded"]:
        advances.append((cid, 3200.00, "funded", date(2023, 6, 1), None))

    pool = by_tag["clean"] + by_tag["real_person_test_lookalike"] + by_tag["staff_real_activity"]
    for cid in random.sample(pool, 95):
        status = random.choices(
            ["funded", "paid_off", "declined", "pending"],
            weights=[30, 35, 20, 15],
        )[0]
        originated = date(2023, 1, 1) + timedelta(days=random.randint(0, 500))
        closed = originated + timedelta(days=random.randint(60, 240)) if status == "paid_off" else None
        advances.append((cid, round(random.uniform(500, 9000), 2), status, originated, closed))

    cur.fast_executemany = True
    cur.executemany(
        """
        INSERT INTO dbo.advances
            (customer_id, principal, status, originated_at, closed_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(c, p, s, o, cl, ts(random.randint(100, 600)), ts(random.randint(1, 99)))
         for c, p, s, o, cl in advances],
    )
    return ids, by_tag


def build_cards(cur, ids, by_tag):
    holders = set(random.sample(ids, int(len(ids) * 0.75)))
    holders.update(by_tag["dup_funded"])
    holders.update(by_tag["dup_both_funded"])
    holders.update(by_tag["dup_plain"])

    cards = []
    for cid in sorted(holders):
        for _ in range(random.choices([1, 2], weights=[80, 20])[0]):
            cards.append(
                (
                    cid,
                    f"{random.randint(0, 9999):04d}",
                    random.choice(["visa", "mastercard", "amex"]),
                    date(2026, 1, 1) + timedelta(days=random.randint(0, 900)),
                    random.choices(["active", "expired", "blocked"], weights=[80, 12, 8])[0],
                    ts(random.randint(100, 650)),
                    ts(random.randint(1, 99)),
                )
            )

    cur.fast_executemany = True
    cur.executemany(
        """
        INSERT INTO dbo.cards
            (customer_id, last_four, brand, expires_on, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        cards,
    )


def build_transactions(cur):
    cur.execute("SELECT card_id, customer_id FROM dbo.cards WHERE status <> 'blocked'")
    pairs = cur.fetchall()
    merchants = [
        "Kroger", "Shell", "Amazon", "Uber", "Starbucks", "Walgreens",
        "Delta Air", "Home Depot", "Netflix", "Chipotle", "CVS", "Target",
    ]

    rows = []
    for _ in range(N_TRANSACTIONS):
        card_id, customer_id = random.choice(pairs)
        occurred = BASE - timedelta(
            days=random.randint(1, 900),
            minutes=random.randint(0, 1439),
        )
        rows.append(
            (
                card_id,
                customer_id,
                round(random.uniform(3, 750), 2),
                "USD",
                random.choice(merchants),
                occurred,
                occurred + timedelta(seconds=random.randint(1, 90)),
            )
        )

    rows.sort(key=lambda r: r[6])
    cur.fast_executemany = True
    for i in range(0, len(rows), 5000):
        cur.executemany(
            """
            INSERT INTO dbo.transactions
                (card_id, customer_id, amount, currency, merchant, occurred_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows[i:i + 5000],
        )


def build_history(cur, ids):
    fields = ["email", "phone", "address_line", "city"]
    rows = []
    for _ in range(1200):
        cid = random.choice(ids)
        field = random.choice(fields)
        changed = BASE - timedelta(days=random.randint(1, 800), minutes=random.randint(0, 1439))
        rows.append(
            (
                cid,
                field,
                fake.word(),
                fake.word(),
                changed,
                random.choice(["ops_console", "customer_portal", "batch_job", "support_agent"]),
            )
        )
    rows.sort(key=lambda r: r[4])
    cur.fast_executemany = True
    cur.executemany(
        """
        INSERT INTO dbo.customer_history
            (customer_id, field_name, old_value, new_value, changed_at, changed_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def build_scratch(cur):
    rows = [
        ("|".join(fake.words(nb=6)), ts(random.randint(1500, 1900)))
        for _ in range(500)
    ]
    cur.fast_executemany = True
    cur.executemany(
        "INSERT INTO dbo.tmp_import_2019 (raw_line, loaded_at) VALUES (?, ?)",
        rows,
    )


def main():
    with connect("master") as master:
        run_schema(master.cursor())

    customers, tags = build_customers()
    extra, extra_tags = build_test_and_trap_customers()
    customers += extra
    tags += extra_tags
    customers = apply_malformed_contacts(customers, tags)

    with connect("opsdb") as conn:
        cur = conn.cursor()
        insert_customers(cur, customers)
        ids, by_tag = build_advances(cur, tags)
        build_cards(cur, ids, by_tag)
        build_transactions(cur)
        build_history(cur, ids)
        build_scratch(cur)

        for table in ["customers", "advances", "cards", "transactions",
                      "customer_history", "tmp_import_2019"]:
            cur.execute(f"SELECT COUNT(*) FROM dbo.{table}")
            print(f"{table:20s} {cur.fetchone()[0]:>8,}")


if __name__ == "__main__":
    sys.exit(main())
