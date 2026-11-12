# Backup-policy experiment matrix

Values are trial medians. Waiting and STREAM tools are separate conditions. Missing measurements are N/A, not zero. Whole time includes inference, tools, reuse tail, drain and counter settling; warmup is excluded.

## Waiting tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.94 (88.94–89.08) | 439.70 | 65.9 | 7.195 | 5.688 | N/A |
| Write-through | Skip | 88.67 (88.60–88.68) | 601.00 | 47.5 | 4.474 | 1.802 | N/A |
| Write-through | Defer: all | 88.97 (88.87–89.16) | 452.55 | 65.9 | 7.195 | 4.067 | N/A |
| Write-through | Defer: victim | 88.83 (88.78–89.08) | 478.35 | 65.9 | 7.195 | 5.688 | N/A |
| Write-through | Defer: through victim | 89.18 (88.85–89.19) | 468.24 | 65.9 | 7.195 | 5.688 | N/A |
| Selective write-through | Normal | 88.69 (88.50–88.73) | 401.91 | 71.5 | 5.402 | 6.852 | N/A |
| Selective write-through | Skip | 88.90 (88.80–89.20) | 609.87 | 43.0 | 1.729 | 0.858 | N/A |
| Selective write-through | Defer: all | 88.23 (88.18–88.47) | 414.25 | 71.5 | 5.402 | 6.852 | N/A |
| Selective write-through | Defer: victim | 88.24 (88.08–88.27) | 434.45 | 71.5 | 5.402 | 6.852 | N/A |
| Selective write-through | Defer: through victim | 88.50 (88.35–88.51) | 423.22 | 71.5 | 5.402 | 6.852 | N/A |
| Write-back | Normal | 88.53 (88.51–88.68) | 434.78 | 74.3 | 8.803 | 7.445 | N/A |
| Write-back | Skip | 88.58 (88.55–88.59) | 433.66 | 74.3 | 8.803 | 7.445 | N/A |
| Write-back | Defer: all | 88.65 (88.62–88.68) | 473.93 | 74.3 | 8.803 | 7.445 | N/A |
| Write-back | Defer: victim | 88.51 (88.22–88.64) | 477.82 | 74.3 | 8.803 | 7.445 | N/A |
| Write-back | Defer: through victim | 88.35 (88.24–88.58) | 433.24 | 74.3 | 8.803 | 7.445 | N/A |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 12.03 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 11.97 | 0 | 111 | 0 | 0 |
| Write-through | Defer: all | 11.99 | 0 | 0 | 24 | 0 |
| Write-through | Defer: victim | 11.94 | 0 | 0 | 13 | 3 |
| Write-through | Defer: through victim | 12.02 | 0 | 0 | 13 | 3 |
| Selective write-through | Normal | 11.81 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 12.25 | 0 | 52 | 0 | 0 |
| Selective write-through | Defer: all | 11.52 | 0 | 0 | 14 | 0 |
| Selective write-through | Defer: victim | 11.45 | 0 | 0 | 13 | 0 |
| Selective write-through | Defer: through victim | 11.62 | 0 | 0 | 13 | 0 |
| Write-back | Normal | 11.64 | 0 | 0 | 29 | 0 |
| Write-back | Skip | 11.84 | 0 | 0 | 29 | 0 |
| Write-back | Defer: all | 11.81 | 0 | 0 | 29 | 0 |
| Write-back | Defer: victim | 11.65 | 0 | 0 | 29 | 0 |
| Write-back | Defer: through victim | 11.59 | 0 | 0 | 29 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 2.758 | 1.837 | 408.48 | 127.56 |
| Write-through | Skip | 0.000 | 2.817 | 621.71 | 232.58 |
| Write-through | Defer: all | 2.758 | 1.837 | 439.04 | 127.61 |
| Write-through | Defer: victim | 2.758 | 1.837 | 468.66 | 128.32 |
| Write-through | Defer: through victim | 2.758 | 1.837 | 468.36 | 127.56 |
| Selective write-through | Normal | 5.325 | 0.078 | 380.24 | 66.47 |
| Selective write-through | Skip | 1.652 | 0.078 | 619.89 | 213.35 |
| Selective write-through | Defer: all | 5.325 | 0.078 | 399.79 | 66.71 |
| Selective write-through | Defer: victim | 5.325 | 0.078 | 421.57 | 67.38 |
| Selective write-through | Defer: through victim | 5.325 | 0.078 | 410.05 | 67.80 |
| Write-back | Normal | 5.538 | 3.237 | 439.27 | 103.60 |
| Write-back | Skip | 5.538 | 3.237 | 437.50 | 101.75 |
| Write-back | Defer: all | 5.538 | 3.237 | 469.06 | 102.79 |
| Write-back | Defer: victim | 5.538 | 3.237 | 447.32 | 103.62 |
| Write-back | Defer: through victim | 5.538 | 3.237 | 437.37 | 103.68 |

## STREAM tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.95 (88.71–89.40) | 376.72 | 65.9 | 7.195 | 5.688 | 36.81 |
| Write-through | Skip | 88.91 (88.88–89.19) | 505.20 | 47.5 | 4.474 | 1.802 | 36.43 |
| Write-through | Defer: all | 88.98 (88.64–89.14) | 384.59 | 65.9 | 7.195 | 5.688 | 36.44 |
| Write-through | Defer: victim | 89.21 (89.20–90.08) | 429.82 | 65.9 | 7.195 | 5.688 | 35.61 |
| Write-through | Defer: through victim | 89.19 (89.01–89.53) | 441.35 | 65.9 | 7.195 | 5.688 | 39.12 |
| Selective write-through | Normal | 88.55 (88.43–89.02) | 375.20 | 71.5 | 5.402 | 6.852 | 36.28 |
| Selective write-through | Skip | 89.50 (89.01–89.66) | 498.25 | 43.0 | 1.729 | 0.858 | 37.06 |
| Selective write-through | Defer: all | 88.38 (88.35–88.45) | 366.11 | 71.5 | 5.402 | 6.852 | 37.34 |
| Selective write-through | Defer: victim | 88.53 (88.41–88.66) | 377.37 | 71.5 | 5.402 | 6.852 | 37.71 |
| Selective write-through | Defer: through victim | 88.62 (88.60–88.62) | 385.93 | 71.5 | 5.402 | 6.852 | 40.02 |
| Write-back | Normal | 88.87 (88.72–88.92) | 406.35 | 74.3 | 8.803 | 7.445 | 36.65 |
| Write-back | Skip | 88.98 (88.71–89.13) | 382.18 | 74.3 | 8.803 | 7.445 | 36.37 |
| Write-back | Defer: all | 88.77 (88.67–88.86) | 400.24 | 74.3 | 8.803 | 7.445 | 36.95 |
| Write-back | Defer: victim | 88.93 (88.74–89.34) | 414.93 | 74.3 | 8.803 | 7.445 | 36.07 |
| Write-back | Defer: through victim | 88.61 (88.50–88.65) | 396.84 | 74.3 | 8.803 | 7.445 | 37.22 |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 11.99 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 12.05 | 0 | 111 | 0 | 0 |
| Write-through | Defer: all | 11.91 | 0 | 0 | 24 | 0 |
| Write-through | Defer: victim | 12.13 | 0 | 0 | 13 | 3 |
| Write-through | Defer: through victim | 11.98 | 0 | 0 | 13 | 3 |
| Selective write-through | Normal | 11.74 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 12.58 | 0 | 52 | 0 | 0 |
| Selective write-through | Defer: all | 11.44 | 0 | 0 | 14 | 0 |
| Selective write-through | Defer: victim | 11.56 | 0 | 0 | 13 | 0 |
| Selective write-through | Defer: through victim | 11.51 | 0 | 0 | 13 | 0 |
| Write-back | Normal | 11.88 | 0 | 0 | 29 | 0 |
| Write-back | Skip | 11.97 | 0 | 0 | 29 | 0 |
| Write-back | Defer: all | 11.72 | 0 | 0 | 29 | 0 |
| Write-back | Defer: victim | 11.69 | 0 | 0 | 29 | 0 |
| Write-back | Defer: through victim | 11.59 | 0 | 0 | 29 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 2.758 | 1.837 | 353.25 | 125.18 |
| Write-through | Skip | 0.000 | 2.817 | 517.09 | 231.27 |
| Write-through | Defer: all | 2.758 | 1.837 | 360.33 | 129.08 |
| Write-through | Defer: victim | 2.758 | 1.837 | 399.22 | 128.32 |
| Write-through | Defer: through victim | 2.758 | 1.837 | 397.67 | 124.86 |
| Selective write-through | Normal | 5.325 | 0.078 | 347.64 | 66.48 |
| Selective write-through | Skip | 1.652 | 0.078 | 509.03 | 209.62 |
| Selective write-through | Defer: all | 5.325 | 0.078 | 342.31 | 66.99 |
| Selective write-through | Defer: victim | 5.325 | 0.078 | 367.76 | 67.91 |
| Selective write-through | Defer: through victim | 5.325 | 0.078 | 373.55 | 67.90 |
| Write-back | Normal | 5.538 | 3.237 | 378.72 | 103.89 |
| Write-back | Skip | 5.538 | 3.237 | 373.27 | 102.51 |
| Write-back | Defer: all | 5.538 | 3.237 | 370.89 | 102.97 |
| Write-back | Defer: victim | 5.538 | 3.237 | 377.89 | 104.67 |
| Write-back | Defer: through victim | 5.538 | 3.237 | 368.78 | 102.55 |

## How to read the comparison

- Compare each controller against normal within the same base policy and tool workload. Across-base differences also change which data is backed up.
- Write-back has no eager optional backups in this implementation. Defer/skip and queue-selection variants may therefore be behaviorally identical; inspect policy activations rather than treating noise as an algorithmic gain.
- Partial selection includes pending ancestor dependencies necessary to preserve a usable host prefix; victim-only is dependency-aware, not a promise of one tensor copy.
- Deferred decisions count scheduler polls; skipped opportunities can revisit entries. Neither is a count of unique cache nodes.
- Every configuration uses the same GPU-space reserve, queue bound and age limit (recorded in status.json). Victim-aware strategies also handle pressure that would otherwise prevent request admission.
- The selected sessions are a narrow, timing-focused TraceLab subset; token lengths remain scaled. STREAM is synthetic memory stress, not original tool execution. No production-level benefit is assumed.
