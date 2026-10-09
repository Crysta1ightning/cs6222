# Initial interference screening

Results: `results/interference-hicache-1791489272616886117.json`.

Ten paired trials, ten repository searches per trial:

| Condition | Median search-batch latency |
| --- | ---: |
| Tool only, inference server idle | 25.932 s |
| Tool with concurrent inference and HiCache | 25.081 s |

Concurrent trials were about 3.3% faster by the ratio of medians. Four pairs were slower and six were faster. This run does not show a consistent slowdown.

All trials recorded zero additional GPU→host backup bytes. Concurrent trials recorded large host→GPU restore byte increases. The reused prompts already had host copies, so this workload primarily exercised restores rather than the optional backups targeted by the proposed controller.

Limitations: transfer counter observations bracket search batches but do not locate physical transfer start/end times; concurrent inference introduces additional CPU/GPU work; the repository resides on the shared home filesystem, which can introduce filesystem noise. No HiCache-disabled control was run yet.

Next: use fresh prompts to exercise GPU→host backups; use a consistent local scratch copy of the repository if available; collect transfer/tool timelines; and compare with HiCache disabled using otherwise identical server settings. This result does not reject the project hypothesis.

## Follow-up: unique-prompt backup workload

Results: `results/interference-hicache-backups-1791490371033274354.json`.

| Condition | Median search-batch latency |
| --- | ---: |
| Tool only, inference server idle | 25.301 s |
| Tool with concurrent inference and HiCache backups | 24.933 s |

Ten pairs again showed no consistent slowdown: concurrent trials were about 1.5% faster by the ratio of medians, and three pairs were slower while seven were faster. Concurrent trials recorded roughly 99–126 GB of GPU→host backup traffic each and zero host→GPU restore bytes.

Seven tool-only trials recorded 229,720,064 backup bytes each. This may reflect asynchronous completion or delayed metric accounting from preceding concurrent trials; physical timing was not measured. These baselines are therefore not verified as transfer-free.

This is a screening result for repository search on the shared home filesystem, not proof that transfers never interfere with CPU tools. Before drawing a causal conclusion, isolate condition boundaries, compare against inference without HiCache, and collect actual transfer timelines.
