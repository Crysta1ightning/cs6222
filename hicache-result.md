# HiCache functionality check

The 8192-token GPU cache / 4 GB host cache configuration passed `check-hicache.py`.

| Phase | GPU→host backup bytes | Host→GPU restore bytes | GPU token slots evicted |
| --- | ---: | ---: | ---: |
| Eight distinct 2048-token prompts | 1,878,360,064 | 0 | 8,204 |
| Revisit first prompt | 0 | 234,536,960 | 2,047 |

This confirms write-through backups, GPU eviction, and reuse through host-to-GPU restoration. It does not establish transfer/tool overlap or tool slowdown.
