# GraphPilot — Core Demo Scenario

> Companion to `GraphPilot_MVP_Scope.md` and `GraphPilot_Graph_Schema.md`. This document defines the seeded ground truth, the investigation steps, the expected results, and the acceptance tests for the primary demo.

**Status:** Planning · **Reference date (`as_of_date`):** 2026-10-15 (fixed in the seed data, not the system clock) · **Data:** Synthetic

---

## 1. Demo Questions

**Primary question (as shown in the UI):**
> "A supplier disruption has occurred. Which products and branches are most at risk because of it, how will the disruption propagate through our supply chain, what evidence supports the root cause, and what is the best recovery action?"

This question names no supplier, so the agent must **discover** the disruption by scanning purchase orders. It also contains five sub-questions, which the agent answers in order:

| # | Sub-question | Answered by |
|---|---|---|
| 1 | Which products and branches are most at risk? | Ranked `affected_entities` |
| 2 | How does the disruption propagate? | `evidence_paths` |
| 3 | What evidence supports the root cause? | `root_cause` + labelled findings |
| 4 | What is the best recovery action? | Ranked `recommendations` |
| 5 | What is uncertain or missing? | `assumptions`, `limitations`, `data_quality_issues` |

**Follow-up question (upstream tracing):**
> "Why is PRD-101 at risk at BR-02?"

---

## 2. Business Scenario

A fictional supplier, **Apex Components Ltd (`SUP-003`)**, has an overdue purchase order, **`PO-1042`**, covering two products delivered to warehouse **`WH-01`**. Three branches are replenished from `WH-01`. Two other branches carry the same product but are served by a different warehouse, `WH-02`.

The user does not know the full impact in advance. GraphPilot investigates the connected records to find it, including one impact that a reorder-level dashboard would miss.

---

## 3. Seeded Ground Truth

All values are synthetic and are the single source of truth for tests. The final demo must **retrieve** them from FalkorDB, not hard-code them.

### 3.1 Purchase orders

| PO | Supplier | Dest. | Lines (`ordered_qty` / `received_qty`) | `order_date` | `expected_date` | `revised_expected_date` | `status` | `delay_reason` |
|---|---|---|---|---|---|---|---|---|
| `PO-1042` | SUP-003 | WH-01 | PRD-101 (800/0), PRD-102 (600/0) | 2026-09-20 | 2026-10-12 | 2026-10-26 | `open` | *null (not recorded)* |
| `PO-1031` | SUP-005 | WH-01 | PRD-103 (fully received) | (past) | (past) | none | `received` | n/a |
| `PO-1050` | SUP-005 | WH-02 | PRD-103 (open, not yet due) | 2026-10-10 | 2026-10-30 | none | `open` | n/a |

`PO-1031` and `PO-1050` are **distractors**. Neither may be flagged as a disruption.

### 3.2 Suppliers

| Supplier | Relevant facts |
|---|---|
| `SUP-003` Apex Components Ltd | Supplies PRD-101 and PRD-102. `lead_time_days: 22`. `reliability_score: 0.72` (synthetic; context only) |
| `SUP-005` | Also supplies PRD-101. `lead_time_days: 9` |

### 3.3 Network and stock for PRD-101 (USB-C Fast Charger 65W)

`WH-01` replenishes BR-01, BR-02, BR-03. `WH-02` replenishes BR-04, BR-05. All five branches `STOCK` PRD-101.

| Location | Stock on hand | Reorder level | Avg daily demand (28-day) |
|---|---|---|---|
| `WH-01` | 420 | n/a | 60 (sum of its branches) |
| `BR-01` | 25 | 50 | 25 |
| `BR-02` | 60 | 40 | 20 |
| `BR-03` | 150 | 30 | 15 |
| `WH-02` | 900 | n/a | 30 (sum of its branches) |
| `BR-04` | 40 | 20 | 18 |
| `BR-05` | 30 | 15 | 12 |

No other open PO delivers PRD-101 to WH-01.

### 3.4 PRD-102 (Wireless Earbuds), negative control

`WH-01` stock 300. Branch stock: BR-01 60, BR-02 40, BR-03 30. Daily demand: BR-01 5, BR-02 4, BR-03 3 (12/day total).

### 3.5 Deliberate data-quality issues

| # | Issue | Seed detail |
|---|---|---|
| DQ1 | **Missing** delay reason | `PO-1042.delay_reason` is null |
| DQ2 | **Contradictory** evidence *(P1, requires `StockMovement`)* | `MOV-0007`: a `receipt` of 100 units of PRD-102 on 2026-10-14, linked `RECEIPT_FOR PO-1042` and `TO_WAREHOUSE WH-01`, while the PRD-102 line shows `received_qty: 0` and the PO `status` is `open` |

If `StockMovement` is cut from the build, DQ2 is dropped and DQ1 alone satisfies the "acknowledges missing evidence" criterion.

---

## 4. Investigation Steps

Template IDs refer to the scope document (§9.1) and schema document (§7).

| # | Step | Template(s) | Evidence produced | Label |
|---|---|---|---|---|
| 1 | **Discover** the disruption. If a supplier or PO is named, start there. Otherwise scan all non-received POs | `disruption_lookup` (T1) | PO records, lines, dates, supplier, destination | Verified fact |
| 2 | **Classify** each PO's `delay_state` against `as_of_date` (schema §6). Expect exactly one `CONFIRMED_DELAY` (`PO-1042`, 14 days late; also 3 days past its original date at `as_of_date`) | none (Python) | `delay_state` per PO | Calculated |
| 3 | **Corroborate**: look for receipt movements for the delayed lines | movement check *(P1)* | "No receipt found for PRD-101 on PO-1042"; DQ2 for PRD-102 | Verified fact / data issue |
| 4 | **Trace** affected products and destinations, applying the exposure rules in schema §5.2 | `downstream_impact` (T2) | PRD-101, PRD-102 → WH-01 → BR-01, BR-02, BR-03 | Verified fact |
| 5 | **Retrieve** current inventory and 28-day sales per affected location and product | `inventory_snapshot` (T3), `demand_by_branch` (T4) | Snapshots and sales aggregates | Verified fact |
| 6 | **Check other inbound supply** for the same product and warehouse | `alternative_inbound` (T5) | Zero rows (a valid verified negative) | Verified fact |
| 7 | **Estimate** cover, stockout dates, exposure, and unmet units where data is sufficient (§5) | none (Python) | Metrics with formulas and inputs | Calculated + assumptions |
| 8 | **Rank** affected branches by explicit risk rules | none (Python) | `AT_RISK` / `WATCH` / `OK` / `UNKNOWN` | Calculated |
| 9 | **Explore recovery options** (§6) | `surplus_candidates` (T6), `alternate_suppliers` (T7) | Donor stock, alternate suppliers | Verified fact + calculated |
| 10 | **Explain** with the evidence path and the reasoning behind each recommendation | none (LLM, from the evidence bundle) | Narrative, validated | Recommendation |

**Discovery behaviour.** If several POs are `CONFIRMED_DELAY`, rank the disruptions by total estimated unmet units. If none are, report that no confirmed disruption was found and list any `OVERDUE_UNCONFIRMED` orders separately.

**Data-sufficiency rule.** Stock cover is estimated only when the location has a current snapshot **and** sales records on at least 14 of the trailing 28 days. Otherwise cover is **not estimated** and the branch is reported as `UNKNOWN`, never guessed. If the 7-day average demand differs from the 28-day average by more than 30%, the result is flagged as demand-sensitive (the seeded demand is constant, so this does not trigger in the demo).

---

## 5. Expected Results (Calculated from the Seeded Data)

### 5.1 Method

Branches are replenished from the warehouse until the warehouse runs out. After that each branch relies on its own stock.

```
warehouse_stockout_date  = as_of_date + (warehouse_stock / warehouse_daily_demand)
branch_stockout_date     = warehouse_stockout_date + (branch_stock / branch_daily_demand)
exposure_days            = max(0, effective_eta − branch_stockout_date)
estimated_unmet_units    = exposure_days × branch_daily_demand
```

`effective_eta` is `revised_expected_date` (2026-10-26 for PO-1042). Risk rules: `AT_RISK` if `exposure_days > 0`; `WATCH` if the stockout falls within 5 days after the ETA; otherwise `OK`.

### 5.2 PRD-101 at WH-01's branches

Warehouse: 420 / 60 = **7.0 days** → stockout **2026-10-22**, which is 4 days before the ETA, so `WH-01` is `AT_RISK` as a source of replenishment.

| Branch | Own cover | Branch stockout | Exposure | Est. unmet units | Risk | Below reorder level? |
|---|---|---|---|---|---|---|
| `BR-01` | 25 / 25 = 1.0 day | 2026-10-23 | 3 days | **75** | `AT_RISK` | **Yes** (25 < 50) |
| `BR-02` | 60 / 20 = 3.0 days | 2026-10-25 | 1 day | **20** | `AT_RISK` | **No** (60 > 40) |
| `BR-03` | 150 / 15 = 10.0 days | 2026-11-01 | 0 | 0 | `OK` (stockout is 6 days after ETA, outside the 5-day watch window) | No |

**Total estimated unmet demand: 95 units.** Ranking: BR-01, then BR-02.

### 5.3 Non-obvious impacts the agent must surface

| # | Finding | Why it is non-obvious |
|---|---|---|
| N1 | `BR-02` is `AT_RISK` although its stock (60) is **above** its reorder level (40) | A reorder-level dashboard flags only BR-01 |
| N2 | `PRD-102` shares `PO-1042` but is **not** at risk (WH-01 cover 300 / 12 = 25 days → 2026-11-09; every branch stockout falls in November) | Shows the agent does not raise false alarms |
| N3 | `BR-04` and `BR-05` carry PRD-101 but are **not exposed**: no path links them to `PO-1042` | A product-level query would implicate all five branches |

### 5.4 Disruption propagation path

```
SUP-003 -HAS_ORDER-> PO-1042
PO-1042 -CONTAINS-> PRD-101 (ordered 800, received 0)
PO-1042 -DELIVERS_TO-> WH-01
WH-01 -REPLENISHES-> BR-01 / BR-02 / BR-03
BR-01 / BR-02 / BR-03 -STOCKS-> PRD-101
snapshots: SNP-WH01-PRD101-20261015, SNP-BR01-PRD101-20261015, SNP-BR02-PRD101-20261015, SNP-BR03-PRD101-20261015
```

---

## 6. Recovery Options and Ranking Rules

### 6.1 Candidate options (computed from the seeded data)

| Option | Computation | Result |
|---|---|---|
| **R1 Transfer from WH-02** | Donor surplus = 900 − (30 × 14) = **480** ≥ 95 needed. Send 75 to BR-01 and 20 to BR-02. Assumed arrival 2026-10-17 (A3), before the earliest branch stockout 2026-10-23. Donor keeps 805 / 30 ≈ **26.8 days** (≥ 14) | Residual unmet demand **0** |
| **R2 Expedite PO-1042 (PRD-101 line)** | To avoid all exposure the line must arrive by **2026-10-23** (earliest branch stockout), 3 days earlier than the revised ETA. Each day gained reduces exposure. Supplier feasibility is unknown | Conditional; supplier-dependent |
| **R3 Order from SUP-005** | If ordered on 2026-10-15 with a 9-day lead time, it arrives 2026-10-24. BR-01 exposure falls from 3 to 1 day (25 units); BR-02 exposure falls to 0 | Residual **25** units (reduction of 70). Requires A4 |

### 6.2 Explicit ranking rules

| Rule | Statement |
|---|---|
| **RR1** | A transfer is eligible if the donor's surplus (stock above 14 days of its own demand, A2) covers the total unmet units, and arrival (`as_of_date` + transfer days, A3) precedes the earliest stockout of the receiving branches |
| **RR2** | Expediting is eligible if the PO is `CONFIRMED_DELAY` and `exposure_days > 0`. Report the latest arrival date that avoids all exposure |
| **RR3** | An alternate supplier is eligible if another supplier `SUPPLIES` the product and `as_of_date` + `lead_time_days` < `effective_eta`. Report the residual exposure |
| **RR4** | Order: (1) immediate mitigations that fully eliminate exposure using only internal resources; (2) actions that correct the cause but depend on the supplier (conditional); (3) partial mitigations by ascending residual unmet units |
| **RR5** | No cost or margin data exists, so options are **not** ranked by cost. The report states this |

**Expected ranking:** R1 (primary), R2 (parallel, addresses the cause), R3 (contingency). The recommendation is the best option **under these explicit rules and assumptions**, not a cost-optimal choice. GraphPilot recommends only; it never creates orders or transfers.

---

## 7. Expected Output

Each element maps to the response format in the scope document (§11).

| Required output | Response field | Demo content |
|---|---|---|
| Identified disruption and supporting records | `root_cause`, `verified_facts` | PO-1042 `CONFIRMED_DELAY`, 14 days; revised ETA 2026-10-26; no receipt for PRD-101 |
| Supplier and PO identifiers | `affected_entities` | `SUP-003`, `PO-1042` |
| Products and branches affected | `affected_entities` | PRD-101 (`AT_RISK`); BR-01, BR-02 (`AT_RISK`); BR-03 (`OK`); PRD-102 (`OK`); BR-04/05 not exposed |
| Inventory and sales evidence | `evidence`, `verified_facts` | Snapshot and 28-day sales aggregates per location |
| Estimated urgency and assumptions | `calculated_estimates`, `assumptions` | Stockout dates, exposure, unmet units; A1–A7 |
| Connected graph path | `evidence_paths` | §5.4 |
| Ranked recovery options | `recommendations` | R1, R2, R3 with supporting finding IDs |
| Missing information or uncertainty | `limitations`, **`data_quality_issues`** (new) | DQ1, DQ2, and the assumptions that carry the most weight |

### 7.1 Assumptions for this scenario

| ID | Assumption |
|---|---|
| A1 | Demand continues at the trailing 28-day average |
| A2 | A donor location keeps 14 days of its own cover |
| A3 | A transfer takes 2 days (not in data) |
| A4 | The alternate supplier's listed lead time holds and capacity exists (not in data) |
| A5 | Branches are replenished from the warehouse until it runs out; branch stock is not drawn down in the meantime |
| A6 | The ETA is treated as the date stock reaches branches; warehouse-to-branch transit is ignored |
| A7 | The donor warehouse can dispatch directly to the receiving branches (the lane is not modelled in the graph) |

### 7.2 Expected summary (illustrative wording)

> PO-1042 from Apex Components (SUP-003) is confirmed delayed by 14 days (revised ETA 2026-10-26); the reason for the delay is not recorded. WH-01 is projected to run out of PRD-101 on 2026-10-22, putting BR-01 (about 75 units short) and BR-02 (about 20 units short) at risk. BR-02 is above its reorder level, so a stock-level dashboard would not flag it. PRD-102 shares the order but is not at risk. The recommended action is to transfer 95 units from WH-02, which keeps about 26.8 days of cover. One record conflict on PRD-102 is noted below; it does not change this conclusion.

### 7.3 `data_quality_issues` (proposed addition to the response)

```json
{
  "issue_id": "DQ2",
  "type": "CONTRADICTION",
  "statement": "MOV-0007 records a receipt of 100 units of PRD-102 for PO-1042 on 2026-10-14, but the PRD-102 line shows received_qty 0 and the PO status is open.",
  "evidence_ids": ["E12", "E13"],
  "impact_on_conclusion": "None for the PRD-101 findings. PRD-102 is not at risk under either reading."
}
```

`type` is `MISSING` (e.g. DQ1) or `CONTRADICTION`. The agent reports the conflict and does not decide which record is correct.

---

## 8. Uncertainty and Limits to State

- The cause of the supplier delay is **not recorded**. GraphPilot states *that* the order is late, not *why*.
- Estimates assume constant demand (A1), no transit time (A6), and an ad-hoc transfer lane (A7).
- Transfer time and alternate-supplier capacity are assumptions, not data (A3, A4).
- No cost, margin, or service-level data exists, so "best" means best under RR1–RR5.
- `reliability_score` is synthetic context, not evidence of cause.
- All data is synthetic and dated 2026-10-15.

---

## 9. Why a Graph Is Necessary

The investigation links several record types through relationship paths. The table compares it with a flat approach.

| Question | Flat table or dashboard | GraphPilot (FalkorDB traversal) |
|---|---|---|
| Which branches does PO-1042 affect? | Join on product only → all 5 branches carrying PRD-101 | `PO → DELIVERS_TO → WH-01 → REPLENISHES → Branch → STOCKS → same Product` → exactly 3 branches |
| Which branches are in trouble? | Stock below reorder level → BR-01 only | Delay + warehouse stockout + branch buffer + demand → BR-01 and BR-02 |
| What else does this disruption touch? | Needs a separate manual query per product | One traversal from the PO reveals PRD-102, then clears it |
| Why is BR-02 at risk? | Not answerable | Reverse traversal: `BR-02 ← WH-01 ← PO-1042 ← SUP-003` |

Without these traversals the agent loses the connected impact analysis that makes the investigation valuable.

---

## 10. Demo Acceptance Criteria

| # | Criterion | Test |
|---|---|---|
| 1 | **Discovery:** with no entity named, the agent identifies `PO-1042` / `SUP-003` as the only `CONFIRMED_DELAY`; `PO-1031` and `PO-1050` are not flagged | End-to-end test |
| 2 | **Non-obvious impacts:** N1, N2, and N3 (§5.3) all appear in the result | Assert on `affected_entities` |
| 3 | **Evidence matches the graph:** re-querying every evidence item by ID returns identical properties | Automated check |
| 4 | **Numbers match ground truth:** 7.0 days, 2026-10-22, BR-01 75 units, BR-02 20 units, total 95 | Calculation unit tests |
| 5 | **Path explanation:** the response explains the chain from `SUP-003` to each affected branch, and `evidence_paths` equals §5.4 | Assert on `evidence_paths` |
| 6 | **Rule-based recommendation:** order is R1, R2, R3 with the quantities and dates in §6.1, and each cites its supporting findings | Assert on `recommendations` |
| 7 | **Missing evidence acknowledged:** the response states that the delay reason is not recorded and offers no invented cause | Assert on `root_cause` and `limitations` |
| 8 | **Contradiction acknowledged:** DQ2 appears in `data_quality_issues` and the conclusion states its impact *(P1)* | Assert on `data_quality_issues` |
| 9 | **Integrity:** every cited ID exists; every narrative number matches a computed value; all recommendations require human approval | Response validator |
| 10 | **Resilience:** if the LLM fails, a deterministic explanation with the same findings is returned | Failure-injection test |
| 11 | **Latency target:** the primary question completes in about 30 seconds or less including LLM calls | Manual timing |

---

## 11. Demo Flow (About 3 Minutes)

1. Click the primary question. GraphPilot returns a ranked summary.
2. Open the graph-path view. The path runs from `SUP-003` through `PO-1042` and `WH-01` to three branches, with BR-04 and BR-05 visibly outside it.
3. Point out BR-02: above its reorder level, yet at risk.
4. Open the evidence panel. Click a finding to see the underlying records.
5. Show the ranked recommendations and the rule that produced the order.
6. Point out the "not recorded" delay reason and the PRD-102 record conflict, to show the agent flags uncertainty instead of inventing a cause.

---

## 12. Updates Required in the Other Documents

| Document | Change |
|---|---|
| Scope §5 and §11 | Replace the warehouse-only numbers (240 unmet units, 4-day exposure, 120-unit alternate-supplier effect) with §5 and §6 here (95 units, branch-level exposure, 25 residual units). Update the example JSON accordingly |
| Scope §7 | Add the discovery scan (no entity named) to the `disruption_impact` intent |
| Scope §9.2–9.3 | Add the branch-buffer exposure formula (§5.1 here) and the `UNKNOWN` risk level |
| Scope §9.5 | Add assumptions A5–A7 |
| Scope §9.6 and §14 | Replace the transfer rule with RR1–RR5; add the new acceptance tests |
| Scope §11 | Add `data_quality_issues` to the response format |
| Schema §11 | Fill in branch snapshot values from §3.3 here |
