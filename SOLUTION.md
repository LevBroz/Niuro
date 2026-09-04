# Solution

I took on all three problems. The incremental load and the checks are finished. The
duplicate resolution runs and produces decisions, but it stops short of writing merges
back to the source, and I explain why at the end.

## Per-table strategy

| Table | Strategy | Watermark | Deletes |
|---|---|---|---|
| `transactions` | append | `transaction_id` | not tracked |
| `customer_history` | append | `history_id` | not tracked |
| `customers` | merge | `updated_at` | key reconciliation |
| `advances` | merge | `updated_at` | key reconciliation |
| `cards` | merge | `updated_at` | key reconciliation |
| `tmp_import_2019` | not replicated | | |

The split is by whether a row can change after it is written. Transactions and history
cannot, so they get a monotonic key and are never read twice. The other three are small
enough that a merge is cheap.

Deletes on the merge tables work by pulling only the primary key column from the source
and removing whatever the warehouse still has. One integer column per table per run.
I did not do this for transactions: a disappearing transaction is a problem in the source
and I would rather the check fail than have the pipeline quietly agree with it.

The source also has an `is_deleted` flag and the loader carries it through. The two
mechanisms catch different things. The flag covers deletes the application does properly,
reconciliation covers everything else.

I left `tmp_import_2019` out of the pipeline. Nothing reads it and nothing has written to
it since 2019.

### Idempotency and recovery

The watermark is committed after the merge, not before. If the process dies in between it
has not moved, so the next run re-reads that window and the merge absorbs it by key.
Re-running a finished load reports zero inserts and zero updates.

`meta.run_log` has a row per table per run with status, watermark range, counts, bytes and
duration. `meta.load_state` holds the committed watermark.

To see a failure and a recovery:

```
python src/load.py --fail-after-stage customers
make load
```

### What this design misses

An update that does not touch `updated_at` is invisible. `make break-stale` reproduces it.
A watermark on an application-maintained column is only as good as the application, and
that is not something the pipeline can fix. Change tracking at the database level is the
answer, and it is the first thing I would add.

## Duplicate customers

### Fields

Identity comes from `national_id` when it is well formed. Everything else in the schema —
email, phone, address, name — narrows a candidate set and nothing more.

The reason for the split is in the seed. Two customers share a phone number, a street
address, and a first and last name. Different dates of birth, different national ids, not
the same person. A rule that scores a shared phone as evidence merges them, and merging
two people is worse than leaving two rows for one person.

Formatting is not a difference. `412-88-1907`, `412881907`, and the same value with a
zero-width character in it are one id. Normalising to digits before comparing is what
makes the group form at all — the seed has all three.

Where the national id is missing or malformed I fall back to surname plus date of birth
plus a valid email, and I treat those groups as lower confidence.

### The four rules

**Funded and paid-off customers.** Marked protected. With one protected record in a group
it survives and the rest merge into it. `protected_customer_never_merged` fails the run if
a protected record is ever the one merging away.

**Two protected records in one group.** Nothing merges. The group goes to
`dq.manual_review` and every member keeps its identity. Two funded advances against one
national id is either a wrong id or fraud. Consolidating two real obligations into one
record is not something I can undo from the warehouse, so a person decides.

**Test data.** A record needs a positive marker and no activity. Corporate domain plus a
test-shaped local part, or a placeholder national id, and only when the customer has no
advances and no transactions. The domain on its own is not a marker — staff run real
business from company addresses and there are two of them in the seed. Neither is a
surname: Testerman, Protestante and Attest all survive.

Excluded accounts drop out of the golden view. They are not deleted.

**Malformed contacts.** Classified in `dq.contact_quality` as valid, malformed or missing,
and left alone. The pipeline cannot tell a typo from a number that belongs to somebody
else, and a silently corrected phone number is harder to notice than a broken one.
Validity is not used as merge evidence either way.

**Cards.** `dq.card_ownership` points them at the survivor and keeps `source_customer_id`
on the row. `main.cards` is untouched. Get this wrong and you charge the wrong person, so
the original owner stays recorded and the mapping is a view. `every_card_has_surviving_owner`
catches the other failure, where a card ends up pointing at nobody.

The resolution never modifies `main.customers`. Decisions live in
`dq.customer_resolution` and consumers read views, so reversing a merge is an update, not
a restore.

## Checks

| Check | Catches |
|---|---|
| `row_count_matches_source` | incomplete loads, either direction |
| `no_rows_deleted_upstream` | source deletes the warehouse kept |
| `no_gaps_in_key_range` | missing rows inside a loaded range |
| `primary_key_is_unique` | a merge that inserted instead of updating |
| `referential_integrity` | orphaned advances, cards, transactions |
| `transaction_amount_reconciles` | value drift with matching row counts |
| `protected_customer_never_merged` | rule one |
| `every_card_has_surviving_owner` | cards stranded by a merge |

Results go to `dq.check_results` with a timestamp so a check that starts failing shows up
as a change.

The amount reconciliation is there because counts alone are weak. Matching counts with a
different sum is what reaches a dashboard and gets found by someone reading a wrong number.

`make demo-failure` drops 40 warehouse rows the source still has and runs the checks.

## Measurements

MEASUREMENTS_PLACEHOLDER

## Cost

Today the bill is a full copy of every table to find a 1% change rate, and the biggest
table is the one that changes least.

Transactions moving to append-only is where the money is. The scratch table costs nothing
once it is out. Key reconciliation on the small tables is one integer column per run.

What is left scales with change rate instead of table size.

## Production

I would put change tracking or CDC on the SQL Server side first, so the watermark stops
depending on the application maintaining `updated_at`. For orchestration, whatever is
already running — a scheduler and a state table covers this shape of problem, and bringing
in an orchestrator for six tables costs more than it returns. dbt once there is more than
one consumer of the golden views.

One-time: the scratch table cleanup and the `NVARCHAR(MAX)` migration. Permanent: the
resolution, plus a uniqueness constraint on normalised national id in the application,
which is the part that actually stops new duplicates.

First delivery would be the checks. Not the incremental load, even though that is the one
that saves money. Right now nothing says whether the warehouse is trustworthy, and the
incremental load is exactly the kind of change whose failure mode is silently missing rows.

## The unbounded identifier

`customers.national_id` is `NVARCHAR(MAX)` and the seed reproduces what that allows:
padding, missing separators, and a zero-width character that makes two identical ids
compare as different.

I left it alone and normalise at comparison time instead of casting on ingest. Casting
means the first value that will not parse either breaks the load or gets truncated, and
whoever notices is looking at a customer count that dropped for no visible reason.

`sql/migrations/001_national_id_type.sql` does it properly. It is not wired into any
target. It needs a window and the team that owns the writes, and doing it from the
ingestion pipeline hides the problem instead of fixing it.

## Skipped

**Slowly changing dimensions.** The warehouse holds current state. Nothing asked for
point-in-time queries and it would have cost me the checks.

**Writing merges back to the source.** The resolution produces decisions and views, not
updates against the operational database. That needs a rollback plan and a person.

**Fuzzy name matching.** No edit distance, no phonetic. It would produce candidate pairs
I cannot adjudicate with what the schema carries, and the risky merges here come from
trusting weak signals in the first place.

**Performance.** Single connection, no parallelism. Not the bottleneck at this volume.
