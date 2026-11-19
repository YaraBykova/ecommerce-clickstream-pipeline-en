# Methodology and detailed results

Results were verified on 19 November 2026 using the complete source archive
covering 24 September 2020 through 28 February 2021.

## Data flow

```text
CSV / ZIP → Python → raw → core → mart → charts
```

- `raw.events_import` stores typed events, source metadata and loading time.
- `raw.import_batches` stores delivery IDs, checksums and processing status.
- `core` contains related user, product, session and event tables.
- `mart` contains views for the funnel, category conversion and data coverage.

Changes to `raw`, the batch ledger and `core` share one transaction. An error
rolls them back. Concurrent loads and the full rebuild use a shared PostgreSQL lock.

## Delivery identity

The source has no individual event ID. A delivery is identified by
**batch ID + record number**. Identical records within a batch are preserved
as separate events.

By default, the ID is the SHA-256 of normalized record values in source order.
The filename and ZIP packaging are excluded. Renaming the CSV or repackaging it
preserves the ID. An explicit ID can be supplied with `--batch-id`.

Replaying an ID with the same content adds zero rows. Changed content under
the same ID causes an error. Different explicit IDs represent different deliveries.
Deduplicating overlapping batches requires an event ID from the source.
The full archive and its split files are verified in separate databases.

## Late events

Event time determines the sequence of actions, regardless of delivery order.
After a batch arrives, its sessions are rebuilt using all their events in `raw`.
The first and last occurrence times of related users and products are also updated.

| Delivery | Event times | Funnel stage reached |
|---|---|---|
| First batch | View at 10:00, purchase at 10:10 | View |
| Late batch | Cart at 10:05 | Purchase |
| Replay of the late batch | Same records | Purchase, 0 new rows |

A late event can make a previously accepted session fail the rules. Its earlier
events are then removed from `core` and related entities are recalculated.
The event history remains in `raw`.

## Session selection

Sessions come from the source's `user_session` field. A session enters `core`
if it has a nonempty ID, exactly one user and a duration of up to 24 hours,
inclusive. Single-event sessions are retained.

| Measure | Count |
|---|---:|
| Raw events | 885,129 |
| Core events | 806,078 |
| Excluded events | 79,051 |
| Core users | 400,263 |
| Core products | 52,608 |
| Core sessions | 479,958 |

| Exclusion reason | Events |
|---|---:|
| Missing session ID | 165 |
| Multiple users in one session | 1,527 |
| Session duration exceeds 24 hours | 78,578 |

Reasons overlap, so their counts do not add up to the excluded-event total.
The 24-hour threshold is a project choice that affects the analyzed population.

## Overall funnel

For every accepted session, find the first view, the first cart event strictly
after it, and the first purchase strictly after that cart event.
Products may differ between steps.

| Stage | Sessions |
|---|---:|
| View | 477,930 |
| Cart | 38,488 |
| Purchase | 19,065 |

View-to-cart conversion is 8.05%, cart-to-purchase is 49.53%, and view-to-purchase
is 3.99%. All percentages in the chart use 477,930 viewing sessions as the base.
The separate cart-to-purchase metric uses 38,488 cart sessions.
Each session counts once per stage. The short stage labels represent completion
of the ordered steps within one session.

The `funnel_stage` field takes `view`, `cart`, `purchase` or `no_view`.
Events with identical timestamps do not form a transition because time must
increase strictly. A purchase requires the preceding steps in order.

## Category funnel

The unit of analysis is a `session_id + category_code` pair with a view.
Different `category_id` values sharing a nonempty name are merged within a session.
Leading and trailing whitespace is removed from category names.

Find the first category view and the first later cart event in the same category.
Products may differ. Repeated actions count as one transition. A session viewing
two categories contributes to both.

| Events in one session | Result |
|---|---|
| View A → cart B | A has a view without a transition. B counts only if it has its own view |
| View A → view B → cart B | A has no transition, B has one |
| View A → view A → cart A → cart A | One pair and one transition for A |
| View A and cart A at the same time | A later cart event is required |

Across 107 named categories, there are 344,777 viewing pairs and 32,571 cart
transitions, a rate of 9.45%. The unit and coverage differ from the overall funnel,
so category totals do not equal the store's unique session count.

Candidates have at least 1,000 viewing sessions and a conversion rate below 9.45%.
They are ranked by sessions without a cart transition. This is a descriptive
way to select larger groups for review. Statistical significance was not assessed.

| Category | Viewing sessions | Reached cart | No transition | Conversion |
|---|---:|---:|---:|---:|
| Phones | 49,264 | 4,261 | 45,003 | 8.65% |
| Printer cartridges | 23,684 | 2,215 | 21,469 | 9.35% |
| Speakers | 15,733 | 900 | 14,833 | 5.72% |
| Laptops | 15,154 | 1,292 | 13,862 | 8.53% |
| TVs | 11,972 | 481 | 11,491 | 4.02% |

## Category-name coverage

| Category name | Core events | Share |
|---|---:|---:|
| Present | 587,072 | 72.83% |
| Missing | 219,006 | 27.17% |

These shares cover events of all types after session selection. The category
comparison covers the named portion. Events with unknown categories remain
in the overall funnel. Looking for names in other events with the same product
or category ID recovered no names. An additional reference dataset is needed.

Investigating conversion differences requires more information on products,
availability and store operations. The observed metrics apply to events in
accepted sessions from this historical period.

## Implementation

- [Full core rebuild](../sql/02_build_core.sql).
- [Session funnel](../sql/04_build_marts.sql).
- [Category conversion and coverage](../sql/06_category_conversion.sql).
- [Batch migration](../sql/08_enable_batches.sql).
- [Affected-session refresh](../sql/09_refresh_sessions.sql).

The analytical marts are regular PostgreSQL views, calculated when queried.
Checks and results are described in the [validation report](../reports/batch-processing.md).

[Project overview](../README.md) · [Setup and tests](setup.md)
