# Tool-Interference-Aware KV-Cache Transfer Scheduling

- **Teammates:** Tai Wei Wu, Wei-Yu Chen, Mani Chandana, Joseph Cuthbert
- **Problem:** Agentic LLM workloads alternate between GPU inference and CPU-side tool execution. Tool-call idle periods can be used to move KV cache between GPU and host memory, reducing future recomputation, but these transfers may contend with co-located tools for host resources. We ask: **Can runtime-aware KV-transfer scheduling reduce end-to-end agent completion time by accounting for this interference, without workflow annotations or future knowledge?**
- **SOTA:** **MORI** exploits tool-call idle windows for KV-cache placement and offloading [1], while **MARS** jointly coordinates CPU tools and GPU inference through scheduling and admission control [2]. Our narrower focus is whether **KV-transfer activity itself delays co-located tools**, and whether changing transfer timing improves end-to-end completion time. We use **SGLang HiCache** as the cache substrate [3], with default HiCache, static transfer-rate limits, and CPU isolation/concurrency controls as baselines.
- **Falsifiable Hypothesis:** When KV transfers and CPU tools compete for shared host resources, dynamically reducing or deferring optional transfers during tool execution will reduce end-to-end completion time compared with default HiCache and static transfer policies. We reject the hypothesis if transfers cause no reproducible tool slowdown, or if dynamic scheduling provides no improvement over tuned static baselines.
- **Solution Direction:** We will instrument SGLang HiCache and a local tool runner to collect tool latency, pending transfer bytes/direction, transfer duration, GPU-memory demand, host-memory availability, and memory-bandwidth pressure where supported. We will first characterize interference using real tools such as repository search and test execution under controlled transfer loads. If the effect is significant, we will implement a lightweight controller that adjusts the **timing and batch size of optional background KV transfers**, while prioritizing required restores and allocation-critical transfers. Evaluation will use **end-to-end task completion time**, supported by tool latency, inference latency, transfer throughput, and recomputed tokens.

## Timeline

| Time | Milestone |
| --- | --- |
| Sep. 18 – Oct. 5 | Set up SGLang/HiCache, tool runner, and instrumentation. |
| Oct. 6 – Oct. 25 | Characterize tool-transfer interference and establish static baselines. |
| Oct. 26 – Nov. 15 | Implement runtime-aware controller if interference is significant. |
| Nov. 16 – Final | Evaluate concurrent agent workloads, run ablations, analyze results, and complete final report. |

## References

[1] “Idleness is Relative: Exploiting Tool-Call Idle Windows for Offloading in Agentic Systems with MORI.” arXiv:2606.00866, 2026.  
https://arxiv.org/abs/2606.00866

[2] “MARS: Efficient, Adaptive Co-Scheduling for Heterogeneous Agentic Systems.” arXiv:2604.26963, 2026.  
https://arxiv.org/abs/2604.26963

[3] SGLang Documentation. “HiCache Design.”  
https://docs.sglang.io/docs/advanced_features/hicache_design
