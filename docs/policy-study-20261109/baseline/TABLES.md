# Backup-policy experiment matrix

Values are trial medians. Waiting and STREAM tools are separate conditions. Missing measurements are N/A, not zero. Whole time includes inference, tools, reuse tail, drain and counter settling; warmup is excluded.

## Waiting tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.28 (87.93–88.50) | 375.76 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Skip | 87.89 (87.85–88.02) | 406.52 | 47.4 | 1.133 | 0.450 | N/A |
| Write-through | Defer: all | 88.24 (88.23–88.31) | 361.84 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Defer: victim | 88.55 (88.33–89.03) | 420.35 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Defer: through victim | 88.17 (87.89–88.70) | 407.65 | 78.8 | 1.152 | 2.104 | N/A |
| Selective write-through | Normal | 88.19 (88.06–88.61) | 403.95 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Skip | 88.03 (87.77–88.11) | 348.20 | 42.9 | 0.435 | 0.214 | N/A |
| Selective write-through | Defer: all | 88.21 (87.94–88.40) | 376.03 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Defer: victim | 88.24 (88.04–88.51) | 376.73 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Defer: through victim | 88.20 (87.81–88.44) | 392.13 | 78.7 | 1.117 | 2.095 | N/A |
| Write-back | Normal | 88.16 (88.01–88.66) | 351.86 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Skip | 88.10 (87.92–88.31) | 411.58 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Defer: all | 88.24 (88.05–88.46) | 321.15 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Defer: victim | 88.29 (88.24–88.50) | 384.72 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Defer: through victim | 88.20 (87.98–88.45) | 378.06 | 78.8 | 1.152 | 2.104 | N/A |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 11.67 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 11.54 | 0 | 84 | 0 | 0 |
| Write-through | Defer: all | 11.82 | 25797 | 0 | 22 | 0 |
| Write-through | Defer: victim | 11.71 | 25744 | 0 | 12 | 3 |
| Write-through | Defer: through victim | 11.60 | 25532 | 0 | 12 | 3 |
| Selective write-through | Normal | 11.72 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 11.70 | 0 | 37 | 0 | 0 |
| Selective write-through | Defer: all | 11.78 | 30203 | 0 | 12 | 0 |
| Selective write-through | Defer: victim | 11.92 | 29701 | 0 | 11 | 0 |
| Selective write-through | Defer: through victim | 11.81 | 30119 | 0 | 11 | 0 |
| Write-back | Normal | 11.72 | 0 | 0 | 23 | 0 |
| Write-back | Skip | 11.72 | 0 | 0 | 21 | 0 |
| Write-back | Defer: all | 11.85 | 0 | 0 | 23 | 0 |
| Write-back | Defer: victim | 11.89 | 0 | 0 | 23 | 0 |
| Write-back | Defer: through victim | 11.70 | 0 | 0 | 23 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 0.833 | 0.024 | 359.99 | 41.05 |
| Write-through | Skip | 0.000 | 0.715 | 400.22 | 49.05 |
| Write-through | Defer: all | 0.711 | 0.024 | 371.05 | 41.16 |
| Write-through | Defer: victim | 0.711 | 0.024 | 461.28 | 44.92 |
| Write-through | Defer: through victim | 0.711 | 0.024 | 441.68 | 43.91 |
| Selective write-through | Normal | 1.042 | 0.022 | 437.54 | 45.66 |
| Selective write-through | Skip | 0.413 | 0.022 | 359.74 | 47.23 |
| Selective write-through | Defer: all | 1.042 | 0.022 | 391.38 | 43.22 |
| Selective write-through | Defer: victim | 1.042 | 0.022 | 387.84 | 43.48 |
| Selective write-through | Defer: through victim | 1.042 | 0.022 | 403.68 | 45.12 |
| Write-back | Normal | 1.060 | 0.081 | 364.83 | 45.82 |
| Write-back | Skip | 1.058 | 0.081 | 413.35 | 46.95 |
| Write-back | Defer: all | 1.060 | 0.081 | 338.86 | 44.58 |
| Write-back | Defer: victim | 1.060 | 0.081 | 366.92 | 45.28 |
| Write-back | Defer: through victim | 1.060 | 0.081 | 378.29 | 45.31 |

## STREAM tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.38 (88.01–88.78) | 316.00 | 78.8 | 1.152 | 2.104 | 36.27 |
| Write-through | Skip | 88.30 (88.02–88.33) | 308.69 | 47.4 | 1.133 | 0.450 | 37.12 |
| Write-through | Defer: all | 88.36 (88.24–88.43) | 314.25 | 78.8 | 1.152 | 2.104 | 36.92 |
| Write-through | Defer: victim | 88.56 (88.29–88.87) | 355.70 | 78.8 | 1.152 | 2.104 | 37.98 |
| Write-through | Defer: through victim | 88.46 (88.41–88.77) | 351.53 | 78.8 | 1.152 | 2.104 | 38.96 |
| Selective write-through | Normal | 88.35 (88.19–88.49) | 316.52 | 78.7 | 1.117 | 2.095 | 37.03 |
| Selective write-through | Skip | 88.02 (87.88–88.34) | 307.90 | 42.9 | 0.435 | 0.214 | 37.14 |
| Selective write-through | Defer: all | 88.22 (88.08–88.24) | 314.77 | 78.7 | 1.117 | 2.095 | 36.85 |
| Selective write-through | Defer: victim | 88.50 (88.40–88.81) | 350.87 | 78.7 | 1.117 | 2.095 | 36.70 |
| Selective write-through | Defer: through victim | 88.39 (88.39–88.62) | 353.86 | 78.7 | 1.117 | 2.095 | 37.20 |
| Write-back | Normal | 88.21 (88.18–88.43) | 316.16 | 78.8 | 1.152 | 2.104 | 36.79 |
| Write-back | Skip | 88.15 (88.09–88.52) | 313.15 | 78.8 | 1.152 | 2.104 | 37.38 |
| Write-back | Defer: all | 88.50 (88.39–88.95) | 308.32 | 78.8 | 1.152 | 2.104 | 37.14 |
| Write-back | Defer: victim | 88.61 (88.45–88.96) | 318.73 | 78.8 | 1.152 | 2.104 | 36.95 |
| Write-back | Defer: through victim | 88.23 (88.22–88.28) | 318.29 | 78.8 | 1.152 | 2.104 | 37.15 |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 11.80 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 11.62 | 0 | 84 | 0 | 0 |
| Write-through | Defer: all | 11.67 | 26132 | 0 | 22 | 0 |
| Write-through | Defer: victim | 11.85 | 26063 | 0 | 12 | 3 |
| Write-through | Defer: through victim | 11.84 | 26052 | 0 | 12 | 3 |
| Selective write-through | Normal | 11.63 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 11.42 | 0 | 37 | 0 | 0 |
| Selective write-through | Defer: all | 11.77 | 30345 | 0 | 12 | 0 |
| Selective write-through | Defer: victim | 11.94 | 29898 | 0 | 11 | 0 |
| Selective write-through | Defer: through victim | 11.77 | 29697 | 0 | 11 | 0 |
| Write-back | Normal | 11.72 | 0 | 0 | 23 | 0 |
| Write-back | Skip | 11.73 | 0 | 0 | 23 | 0 |
| Write-back | Defer: all | 12.00 | 0 | 0 | 23 | 0 |
| Write-back | Defer: victim | 11.93 | 0 | 0 | 23 | 0 |
| Write-back | Defer: through victim | 11.55 | 0 | 0 | 23 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 0.711 | 0.024 | 342.13 | 43.38 |
| Write-through | Skip | 0.000 | 0.715 | 340.27 | 48.97 |
| Write-through | Defer: all | 0.711 | 0.024 | 338.20 | 41.65 |
| Write-through | Defer: victim | 0.711 | 0.024 | 370.20 | 44.57 |
| Write-through | Defer: through victim | 0.711 | 0.024 | 365.74 | 41.90 |
| Selective write-through | Normal | 1.042 | 0.022 | 340.34 | 43.50 |
| Selective write-through | Skip | 0.413 | 0.022 | 336.56 | 46.76 |
| Selective write-through | Defer: all | 1.042 | 0.022 | 339.13 | 42.76 |
| Selective write-through | Defer: victim | 1.042 | 0.022 | 366.47 | 42.69 |
| Selective write-through | Defer: through victim | 1.042 | 0.022 | 364.56 | 43.23 |
| Write-back | Normal | 1.060 | 0.081 | 341.62 | 44.83 |
| Write-back | Skip | 1.060 | 0.081 | 336.11 | 46.25 |
| Write-back | Defer: all | 1.060 | 0.081 | 329.31 | 44.28 |
| Write-back | Defer: victim | 1.060 | 0.081 | 345.11 | 45.47 |
| Write-back | Defer: through victim | 1.060 | 0.081 | 338.36 | 48.27 |

## How to read the comparison

- Compare each controller against normal within the same base policy and tool workload. Across-base differences also change which data is backed up.
- Write-back has no eager optional backups in this implementation. Defer/skip and queue-selection variants may therefore be behaviorally identical; inspect policy activations rather than treating noise as an algorithmic gain.
- Partial selection includes pending ancestor dependencies necessary to preserve a usable host prefix; victim-only is dependency-aware, not a promise of one tensor copy.
- Deferred decisions count scheduler polls; skipped opportunities can revisit entries. Neither is a count of unique cache nodes.
- Every configuration uses the same GPU-space reserve, queue bound and age limit (recorded in status.json). Victim-aware strategies also handle pressure that would otherwise prevent request admission.
- The selected sessions are a narrow, timing-focused TraceLab subset; token lengths remain scaled. STREAM is synthetic memory stress, not original tool execution. No production-level benefit is assumed.
