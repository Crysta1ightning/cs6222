# Complete configuration comparisons

Every cell uses three accepted trials. Changes are relative to normal within the same profile, base policy and tool condition. Negative time/latency/copy changes mean lower values; positive block-rate changes mean more completed work per worker-second. These are descriptive medians, not significance tests. Full min/max ranges are in each summary CSV.

## baseline

| Configuration | Delay trials | STREAM trials |
|---|---:|---:|
| write_back--defer--all | 3/3 | 3/3 |
| write_back--defer--through_victim | 3/3 | 3/3 |
| write_back--defer--victim | 3/3 | 3/3 |
| write_back--normal--all | 3/3 | 3/3 |
| write_back--skip--all | 3/3 | 3/3 |
| write_through--defer--all | 3/3 | 3/3 |
| write_through--defer--through_victim | 3/3 | 3/3 |
| write_through--defer--victim | 3/3 | 3/3 |
| write_through--normal--all | 3/3 | 3/3 |
| write_through--skip--all | 3/3 | 3/3 |
| write_through_selective--defer--all | 3/3 | 3/3 |
| write_through_selective--defer--through_victim | 3/3 | 3/3 |
| write_through_selective--defer--victim | 3/3 | 3/3 |
| write_through_selective--normal--all | 3/3 | 3/3 |
| write_through_selective--skip--all | 3/3 | 3/3 |

### delay: change from normal

| Configuration | Execution time | Request latency | D2H bytes | STREAM blocks/s |
|---|---:|---:|---:|---:|
| write_back--defer--all | -0.05% | -8.73% | +0.00% | N/A |
| write_back--defer--through_victim | -0.00% | +7.45% | +0.00% | N/A |
| write_back--defer--victim | -0.02% | +9.34% | +0.00% | N/A |
| write_back--skip--all | -0.20% | +16.97% | +0.00% | N/A |
| write_through--defer--all | -0.06% | -3.71% | +0.00% | N/A |
| write_through--defer--through_victim | -0.07% | +8.49% | +0.00% | N/A |
| write_through--defer--victim | +0.25% | +11.86% | +0.00% | N/A |
| write_through--skip--all | -0.26% | +8.19% | -1.67% | N/A |
| write_through_selective--defer--all | +0.07% | -6.91% | +0.00% | N/A |
| write_through_selective--defer--through_victim | -0.04% | -2.93% | +0.00% | N/A |
| write_through_selective--defer--victim | +0.03% | -6.74% | +0.00% | N/A |
| write_through_selective--skip--all | -0.19% | -13.80% | -61.07% | N/A |

### stream: change from normal

| Configuration | Execution time | Request latency | D2H bytes | STREAM blocks/s |
|---|---:|---:|---:|---:|
| write_back--defer--all | +0.18% | -2.48% | +0.00% | +0.22% |
| write_back--defer--through_victim | +0.16% | +0.67% | +0.00% | +0.25% |
| write_back--defer--victim | +0.33% | +0.81% | +0.00% | -0.45% |
| write_back--skip--all | -0.01% | -0.95% | +0.00% | +0.36% |
| write_through--defer--all | +0.12% | -0.55% | +0.00% | +0.25% |
| write_through--defer--through_victim | +0.19% | +11.24% | +0.00% | +1.05% |
| write_through--defer--victim | +0.17% | +12.56% | +0.00% | +0.60% |
| write_through--skip--all | +0.01% | -2.32% | -1.67% | -0.63% |
| write_through_selective--defer--all | -0.25% | -0.55% | +0.00% | -0.26% |
| write_through_selective--defer--through_victim | +0.02% | +11.80% | +0.00% | +0.51% |
| write_through_selective--defer--victim | +0.13% | +10.85% | +0.00% | +0.73% |
| write_through_selective--skip--all | -0.22% | -2.72% | -61.07% | +0.01% |

### STREAM activation and work

Counts are medians across trials. Polls/opportunities can revisit an entry. Peak pending tokens can exceed a flush threshold within an atomic operation.

| Configuration | Deferred decisions | Pressure overrides | Partial selections | Peak pending tokens | Blocks | Blocks/worker-s |
|---|---:|---:|---:|---:|---:|---:|
| write_back--defer--all | 0 | 23 | 0 | 2850 | 541 | 3.067 |
| write_back--defer--through_victim | 0 | 23 | 0 | 2850 | 539 | 3.067 |
| write_back--defer--victim | 0 | 23 | 0 | 2850 | 534 | 3.046 |
| write_back--normal--all | 0 | 23 | 0 | 2850 | 540 | 3.060 |
| write_back--skip--all | 0 | 23 | 0 | 2850 | 540 | 3.071 |
| write_through--defer--all | 26132 | 22 | 0 | 2850 | 540 | 3.066 |
| write_through--defer--through_victim | 26052 | 12 | 3 | 2850 | 543 | 3.090 |
| write_through--defer--victim | 26063 | 12 | 3 | 2850 | 542 | 3.076 |
| write_through--normal--all | 0 | 0 | 0 | 2850 | 537 | 3.058 |
| write_through--skip--all | 0 | 0 | 0 | 3098 | 535 | 3.039 |
| write_through_selective--defer--all | 30345 | 12 | 0 | 2850 | 536 | 3.044 |
| write_through_selective--defer--through_victim | 29697 | 11 | 0 | 2850 | 539 | 3.067 |
| write_through_selective--defer--victim | 29898 | 11 | 0 | 2850 | 540 | 3.074 |
| write_through_selective--normal--all | 0 | 0 | 0 | 2850 | 537 | 3.052 |
| write_through_selective--skip--all | 0 | 0 | 0 | 2523 | 536 | 3.052 |
## token4x

| Configuration | Delay trials | STREAM trials |
|---|---:|---:|
| write_back--defer--all | 3/3 | 3/3 |
| write_back--defer--through_victim | 3/3 | 3/3 |
| write_back--defer--victim | 3/3 | 3/3 |
| write_back--normal--all | 3/3 | 3/3 |
| write_back--skip--all | 3/3 | 3/3 |
| write_through--defer--all | 3/3 | 3/3 |
| write_through--defer--through_victim | 3/3 | 3/3 |
| write_through--defer--victim | 3/3 | 3/3 |
| write_through--normal--all | 3/3 | 3/3 |
| write_through--skip--all | 3/3 | 3/3 |
| write_through_selective--defer--all | 3/3 | 3/3 |
| write_through_selective--defer--through_victim | 3/3 | 3/3 |
| write_through_selective--defer--victim | 3/3 | 3/3 |
| write_through_selective--normal--all | 3/3 | 3/3 |
| write_through_selective--skip--all | 3/3 | 3/3 |

### delay: change from normal

| Configuration | Execution time | Request latency | D2H bytes | STREAM blocks/s |
|---|---:|---:|---:|---:|
| write_back--defer--all | +0.03% | +9.01% | +0.00% | N/A |
| write_back--defer--through_victim | -0.22% | -0.35% | +0.00% | N/A |
| write_back--defer--victim | -0.02% | +9.90% | +0.00% | N/A |
| write_back--skip--all | -0.05% | -0.26% | +0.00% | N/A |
| write_through--defer--all | +0.06% | +2.92% | +0.00% | N/A |
| write_through--defer--through_victim | +0.29% | +6.49% | +0.00% | N/A |
| write_through--defer--victim | -0.07% | +8.79% | +0.00% | N/A |
| write_through--skip--all | -0.23% | +36.68% | -37.82% | N/A |
| write_through_selective--defer--all | -0.23% | +3.07% | +0.00% | N/A |
| write_through_selective--defer--through_victim | -0.09% | +5.30% | +0.00% | N/A |
| write_through_selective--defer--victim | -0.22% | +8.10% | +0.00% | N/A |
| write_through_selective--skip--all | +0.43% | +51.74% | -67.99% | N/A |

### stream: change from normal

| Configuration | Execution time | Request latency | D2H bytes | STREAM blocks/s |
|---|---:|---:|---:|---:|
| write_back--defer--all | -0.10% | -1.50% | +0.00% | +0.18% |
| write_back--defer--through_victim | -0.28% | -2.34% | +0.00% | +0.02% |
| write_back--defer--victim | +0.13% | +2.11% | +0.00% | -0.43% |
| write_back--skip--all | -0.11% | -5.95% | +0.00% | +0.04% |
| write_through--defer--all | +0.09% | +2.09% | +0.00% | +0.19% |
| write_through--defer--through_victim | +0.24% | +17.16% | +0.00% | +0.86% |
| write_through--defer--victim | +0.28% | +14.10% | +0.00% | -6.40% |
| write_through--skip--all | +0.07% | +34.10% | -37.82% | -1.56% |
| write_through_selective--defer--all | +0.01% | -2.42% | +0.00% | -0.66% |
| write_through_selective--defer--through_victim | +0.26% | +2.86% | +0.00% | +0.51% |
| write_through_selective--defer--victim | +0.15% | +0.58% | +0.00% | -0.81% |
| write_through_selective--skip--all | +1.04% | +32.80% | -67.99% | -0.39% |

### STREAM activation and work

Counts are medians across trials. Polls/opportunities can revisit an entry. Peak pending tokens can exceed a flush threshold within an atomic operation.

| Configuration | Deferred decisions | Pressure overrides | Partial selections | Peak pending tokens | Blocks | Blocks/worker-s |
|---|---:|---:|---:|---:|---:|---:|
| write_back--defer--all | 0 | 29 | 0 | 8192 | 540 | 3.072 |
| write_back--defer--through_victim | 0 | 29 | 0 | 8192 | 540 | 3.067 |
| write_back--defer--victim | 0 | 29 | 0 | 8192 | 536 | 3.054 |
| write_back--normal--all | 0 | 29 | 0 | 8192 | 539 | 3.067 |
| write_back--skip--all | 0 | 29 | 0 | 8192 | 540 | 3.068 |
| write_through--defer--all | 0 | 24 | 0 | 8192 | 537 | 3.049 |
| write_through--defer--through_victim | 0 | 13 | 3 | 8192 | 540 | 3.069 |
| write_through--defer--victim | 0 | 13 | 3 | 8192 | 502 | 2.848 |
| write_through--normal--all | 0 | 0 | 0 | 12539 | 536 | 3.043 |
| write_through--skip--all | 0 | 0 | 0 | 12539 | 528 | 2.996 |
| write_through_selective--defer--all | 0 | 14 | 0 | 8192 | 537 | 3.053 |
| write_through_selective--defer--through_victim | 0 | 13 | 0 | 8192 | 544 | 3.089 |
| write_through_selective--defer--victim | 0 | 13 | 0 | 8192 | 537 | 3.049 |
| write_through_selective--normal--all | 0 | 0 | 0 | 11399 | 540 | 3.074 |
| write_through_selective--skip--all | 0 | 0 | 0 | 10091 | 538 | 3.062 |
