# Batch and late-event validation

Verified on 19 November 2026 with PostgreSQL 15 using the full `events.csv.zip`
archive of 885,129 events. Checks ran in a separate temporary database.
The source archive was used read-only.

## Full-data run

The archive was split into nine CSV batches, eight containing 100,000 rows
and the last containing 85,129. After each delivery, affected sessions and
their related users and products were refreshed.

| Check | Result |
|---|---:|
| Raw events after nine batches | 885,129 |
| Core events | 806,078 |
| Core sessions | 479,958 |
| Registered batches | 9 |
| Batches awaiting core processing | 0 |
| New rows after replaying every batch | 0 |
| Refreshed sessions during replay | 0 |
| Differences from a full rebuild | 0 |

Replaying deliveries left the batch ledger unchanged, including registration
and core-application timestamps. The funnel contained 477,930 sessions with
a view, 38,488 with a subsequent cart event, and 19,065 with a purchase after it.

## Comparison with a full rebuild

Snapshots of every column in nine tables and views were saved before running
`02_build_core.sql` against the same raw data. A bidirectional `EXCEPT ALL`
comparison, which preserves row multiplicity, returned zero differences
for every object.

- `core.users`, `core.products`, `core.sessions`, `core.events`.
- `mart.session_funnel`, `mart.funnel_summary`.
- `mart.session_category_funnel`, `mart.category_conversion`.
- `mart.category_data_quality`.

The 107 named categories contained 344,777 viewing pairs and 32,571 cart
transitions. The mismatch counters in `05_check_marts.sql` and
`07_check_category_conversion.sql` were all zero. Charts were regenerated
from the verified views.

## Clean-install verification

The project was also exported from Git without local settings or an existing
Python environment. Dependencies were installed from `requirements.txt` into
a new environment, with a separate PostgreSQL 15 instance. All 21 tests passed.

Using the documented CLI commands, the full archive and nine batches were loaded
into two independent databases. Replays added zero rows. A row-by-row comparison
of every column in all nine tables and views confirmed identical results.
Regenerated Russian-edition charts matched the committed images byte for byte.

Verified versions were Python 3.14.6, psycopg 3.3.9, Matplotlib 3.11.2 and NumPy 2.5.5.

The English edition also passed all 21 integration tests. Its full-source replay
added zero rows, and its charts were generated from the same verified views.
SQL statements match the Russian edition, with only comments translated.

## Adoption of an earlier load

The earlier raw schema without batch metadata was reproduced in an isolated
database. The full archive was passed to the regular `run_pipeline.py` command.
After record-by-record comparison, all 885,129 existing rows were registered
with zero new inserts. Their IDs were preserved. All nine tables and views
matched the saved snapshots again.

If existing rows differ from the supplied source, adoption fails and rolls back.
This prevents an earlier load from being mixed with a new delivery when the
identity of the earlier records is unknown.

## Integration tests

All [21 tests](../tests/test_batches.py) passed against a real PostgreSQL instance.
Each test creates and removes a separate database.

Cases cover a late cart event between a view and purchase, redelivery, changed
content under an existing ID, identical actions, ZIP repackaging, CSV and price
errors, rollback after a core failure, concurrent delivery, excessive session
duration, a second user appearing in a session, adoption of earlier data,
file splitting and CLI execution. An additional check verifies that an
unaffected session retains its existing row version.

## Scope of the replay guarantee

Delivery identity consists of a batch ID and record number. Identical records
within a batch are preserved. Different explicit IDs represent different
deliveries. Deduplicating overlaps between batches requires a stable source event ID.

Configuration and commands are in the [setup guide](../docs/setup.md).
