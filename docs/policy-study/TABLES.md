# Backup-policy experiment matrix

Values are trial medians. Waiting and STREAM tools are separate conditions. Missing measurements are N/A, not zero. Whole time includes inference, tools, reuse tail, drain and counter settling; warmup is excluded.

## Waiting tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.15 (88.00–88.25) | 324.20 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Skip | 88.10 (87.95–88.19) | 316.16 | 47.4 | 1.133 | 0.450 | N/A |
| Write-through | Defer: all | 87.99 (87.95–88.19) | 378.73 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Defer: victim | 88.32 (88.17–88.38) | 386.63 | 78.8 | 1.152 | 2.104 | N/A |
| Write-through | Defer: through victim | 88.46 (88.35–88.50) | 360.61 | 78.8 | 1.152 | 2.104 | N/A |
| Selective write-through | Normal | 88.16 (87.97–88.24) | 345.59 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Skip | 87.88 (87.68–87.93) | 325.03 | 42.9 | 0.435 | 0.214 | N/A |
| Selective write-through | Defer: all | 88.12 (87.95–88.25) | 375.74 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Defer: victim | 88.01 (87.89–88.11) | 371.22 | 78.7 | 1.117 | 2.095 | N/A |
| Selective write-through | Defer: through victim | 88.05 (88.04–88.29) | 378.77 | 78.7 | 1.117 | 2.095 | N/A |
| Write-back | Normal | 88.30 (88.06–88.61) | 344.27 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Defer: all | 88.12 (87.98–88.33) | 350.10 | 78.8 | 1.152 | 2.104 | N/A |
| Write-back | Defer: through victim | 88.40 (88.10–88.50) | 353.22 | 78.8 | 1.152 | 2.104 | N/A |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 11.67 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 11.77 | 0 | 84 | 0 | 0 |
| Write-through | Defer: all | 11.58 | 25887 | 0 | 23 | 0 |
| Write-through | Defer: victim | 11.64 | 25685 | 0 | 12 | 3 |
| Write-through | Defer: through victim | 11.64 | 25885 | 0 | 12 | 3 |
| Selective write-through | Normal | 11.61 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 11.65 | 0 | 37 | 0 | 0 |
| Selective write-through | Defer: all | 11.66 | 29815 | 0 | 12 | 0 |
| Selective write-through | Defer: victim | 11.57 | 30193 | 0 | 11 | 0 |
| Selective write-through | Defer: through victim | 11.58 | 29925 | 0 | 11 | 0 |
| Write-back | Normal | 11.82 | 0 | 0 | 23 | 0 |
| Write-back | Defer: all | 11.55 | 0 | 0 | 23 | 0 |
| Write-back | Defer: through victim | 11.84 | 0 | 0 | 23 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 0.711 | 0.024 | 338.10 | 38.95 |
| Write-through | Skip | 0.000 | 0.715 | 335.84 | 46.19 |
| Write-through | Defer: all | 0.833 | 0.024 | 363.95 | 43.31 |
| Write-through | Defer: victim | 0.711 | 0.024 | 414.91 | 43.09 |
| Write-through | Defer: through victim | 0.711 | 0.024 | 376.36 | 42.28 |
| Selective write-through | Normal | 1.042 | 0.022 | 348.43 | 42.72 |
| Selective write-through | Skip | 0.413 | 0.022 | 339.40 | 44.65 |
| Selective write-through | Defer: all | 1.042 | 0.022 | 387.57 | 42.61 |
| Selective write-through | Defer: victim | 1.042 | 0.022 | 398.74 | 43.59 |
| Selective write-through | Defer: through victim | 1.042 | 0.022 | 398.15 | 44.60 |
| Write-back | Normal | 1.060 | 0.081 | 356.11 | 47.53 |
| Write-back | Defer: all | 1.060 | 0.081 | 368.57 | 46.71 |
| Write-back | Defer: through victim | 1.060 | 0.081 | 354.93 | 44.28 |

## STREAM tools

| Base policy | Controller | Whole time, s (min–max) | Request latency, ms | Input reuse, % | D2H, GB | H2D, GB | STREAM, GB/s |
|---|---|---:|---:|---:|---:|---:|---:|
| Write-through | Normal | 88.32 (88.30–88.91) | 323.97 | 78.8 | 1.152 | 2.104 | 35.53 |
| Write-through | Skip | 88.38 (88.11–88.50) | 314.70 | 47.4 | 1.133 | 0.450 | 35.30 |
| Write-through | Defer: all | 88.41 (88.28–88.67) | 346.12 | 78.8 | 1.152 | 2.104 | 31.53 |
| Write-through | Defer: victim | 88.55 (88.54–88.82) | 383.38 | 78.8 | 1.152 | 2.104 | 32.91 |
| Write-through | Defer: through victim | 88.56 (88.30–89.02) | 360.42 | 78.8 | 1.152 | 2.104 | 36.88 |
| Selective write-through | Normal | 88.41 (88.19–88.47) | 325.09 | 78.7 | 1.117 | 2.095 | 35.54 |
| Selective write-through | Skip | 88.09 (88.08–88.45) | 321.10 | 42.9 | 0.435 | 0.214 | 35.40 |
| Selective write-through | Defer: all | 88.33 (88.09–88.40) | 312.20 | 78.7 | 1.117 | 2.095 | 37.13 |
| Selective write-through | Defer: victim | 88.35 (88.24–88.54) | 371.26 | 78.7 | 1.117 | 2.095 | 31.46 |
| Selective write-through | Defer: through victim | 88.40 (88.25–88.72) | 382.41 | 78.7 | 1.117 | 2.095 | 35.07 |
| Write-back | Normal | 88.69 (88.51–88.69) | 344.38 | 78.8 | 1.152 | 2.104 | 30.34 |
| Write-back | Defer: all | 88.52 (88.50–88.75) | 343.69 | 78.8 | 1.152 | 2.104 | 31.10 |
| Write-back | Defer: through victim | 88.38 (88.35–88.59) | 320.54 | 78.8 | 1.152 | 2.104 | 35.07 |

| Base policy | Controller | Post-tool time, s | Deferred decisions | Skipped opportunities | Safety overrides | Partial reclaim selections |
|---|---|---:|---:|---:|---:|---:|
| Write-through | Normal | 11.60 | 0 | 0 | 0 | 0 |
| Write-through | Skip | 11.63 | 0 | 84 | 0 | 0 |
| Write-through | Defer: all | 11.60 | 26070 | 0 | 22 | 0 |
| Write-through | Defer: victim | 11.70 | 25783 | 0 | 12 | 3 |
| Write-through | Defer: through victim | 11.47 | 26289 | 0 | 12 | 3 |
| Selective write-through | Normal | 11.58 | 0 | 0 | 0 | 0 |
| Selective write-through | Skip | 11.71 | 0 | 37 | 0 | 0 |
| Selective write-through | Defer: all | 11.56 | 29792 | 0 | 12 | 0 |
| Selective write-through | Defer: victim | 11.60 | 30015 | 0 | 11 | 0 |
| Selective write-through | Defer: through victim | 11.65 | 30034 | 0 | 11 | 0 |
| Write-back | Normal | 11.95 | 0 | 0 | 23 | 0 |
| Write-back | Defer: all | 11.74 | 0 | 0 | 23 | 0 |
| Write-back | Defer: through victim | 11.74 | 0 | 0 | 23 | 0 |

| Base policy | Controller | Tool-window D2H, GB | Post-tool D2H, GB | Tool-window request latency, ms | Post-tool request latency, ms |
|---|---|---:|---:|---:|---:|
| Write-through | Normal | 0.711 | 0.024 | 351.65 | 39.35 |
| Write-through | Skip | 0.000 | 0.715 | 350.02 | 48.20 |
| Write-through | Defer: all | 0.711 | 0.024 | 369.99 | 43.10 |
| Write-through | Defer: victim | 0.711 | 0.024 | 400.75 | 43.28 |
| Write-through | Defer: through victim | 0.711 | 0.024 | 377.49 | 39.85 |
| Selective write-through | Normal | 1.042 | 0.022 | 350.27 | 42.37 |
| Selective write-through | Skip | 0.413 | 0.022 | 357.35 | 42.31 |
| Selective write-through | Defer: all | 1.042 | 0.022 | 334.82 | 43.29 |
| Selective write-through | Defer: victim | 1.042 | 0.022 | 382.22 | 44.61 |
| Selective write-through | Defer: through victim | 1.042 | 0.022 | 380.82 | 43.32 |
| Write-back | Normal | 1.060 | 0.081 | 367.44 | 48.61 |
| Write-back | Defer: all | 1.060 | 0.081 | 368.40 | 49.27 |
| Write-back | Defer: through victim | 1.060 | 0.081 | 346.24 | 44.66 |

## How to read the comparison

- Compare each controller against normal within the same base policy and tool workload. Across-base differences also change which data is backed up.
- Write-back has no eager optional backups in this implementation. Defer/skip and queue-selection variants may therefore be behaviorally identical; inspect policy activations rather than treating noise as an algorithmic gain.
- Partial selection includes pending ancestor dependencies necessary to preserve a usable host prefix; victim-only is dependency-aware, not a promise of one tensor copy.
- Deferred decisions count scheduler polls; skipped opportunities can revisit entries. Neither is a count of unique cache nodes.
- Every configuration uses the same GPU-space reserve, queue bound and age limit (recorded in status.json). Victim-aware strategies also handle pressure that would otherwise prevent request admission.
- The selected sessions are a narrow, timing-focused TraceLab subset; token lengths remain scaled. STREAM is synthetic memory stress, not original tool execution. No production-level benefit is assumed.
