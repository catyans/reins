# Reins: reducing model work without lowering acceptance

Real API experiment; constructed invoices, not customer production data.

Frozen validation selection: **compact-batch-16**. Audit: **100 new documents**.
All policies use the same model, identity alignment and deterministic amount calculator.

| Policy | Accepted | USD / 1,000 accepted | Documents / second | p95 batch service | API calls |
|---|---:|---:|---:|---:|---:|
| single-lite | 100/100 | $0.06520 | 0.58 | 2.48s | 100 |
| verbose-batch-4 | 99/100 | $0.06018 | 1.64 | 3.32s | 28 |
| compact-batch-16 | 100/100 | $0.03259 | 4.36 | 5.79s | 9 |
| compact-batch-4 | 100/100 | $0.03662 | 2.15 | 2.36s | 25 |

## Comparisons

- Versus single-lite: 50.0% lower cost per accepted invoice; 7.53× measured sequential-job throughput; +0.0% acceptance difference.
- Versus verbose-batch-4: 45.8% lower cost per accepted invoice; 2.66× measured sequential-job throughput; +1.0% acceptance difference.
- Versus compact-batch-4: 11.0% lower cost per accepted invoice; 2.03× measured sequential-job throughput; +0.0% acceptance difference.

## What changed

- Multiple independent documents share task instructions in one model request.
- Compact arrays avoid repeating field names and unneeded evidence text. The evaluated deliverable is the same five business fields for all policies; do not use this configuration if a customer requires evidence quotes.
- Results are matched by invoice identity, not output order. Missing or invalid items alone use single-item fallback.
- Explicit tax-free arithmetic is computed with Decimal from source values. Every baseline receives the same calculator and response alignment.
- Validation selects batch size; the audit does not select again.

## Limits

- These are one provider, one constructed workload and shared document templates. New IDs and amounts are disjoint, not a wholly independent distribution.
- Throughput is documents completed per second of a sequential job. It is not serving throughput at fixed concurrency. Larger batches can increase individual completion latency. Queueing behind earlier batches is excluded from the batch-service p95.
- Cost is returned tokens times standard paid API prices, not a billing debit. Local CPU/machine cost is not monetized. No search or paid judge was used.
- A perfect 100-case audit does not prove a zero production error rate. This is an engineering result, not yet a proprietary research moat.

Total calls including the additional baseline: 221; list-price estimate: $0.026554. Ledger agreement: True.
Compact batch 4, post-hoc stress comparison; no reselection
