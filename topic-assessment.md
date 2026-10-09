# Topic assessment: tool-aware KV backup scheduling

Assessment date: October 8, 2026.

## Recommendation

Do not commit to the proposed dynamic controller yet. Our experiments establish working HiCache transfers, no consistent repository-search slowdown, and a small synthetic memory-bandwidth difference. They do not establish an end-to-end benefit from changing backup timing.

If the course accepts an empirical systems study, the strongest continuation is a narrower characterization of when KV write policies help or hurt agent workloads. If the course requires a new optimization with demonstrated gains, changing topics now is reasonable. Neither decision requires declaring that host-resource interference is impossible.

## What the literature already addresses

| Source | Existing contribution | Consequence for our proposal |
| --- | --- | --- |
| [Official HiCache introduction](https://www.lmsys.org/blog/2025-09-10-sglang-hicache/) | Cache reuse across memory tiers; optimized transfers; multiple write policies. Write-through benefits are conditional on bandwidth availability. | A no-reuse workload measures costs while excluding the intended benefit. Compare against selective write-through and write-back, not just HiCache disabled. |
| [Strata, §§4.2–4.3 and evaluation](https://arxiv.org/html/2508.18572v1) | GPU-assisted I/O, measured interference with GPU inference, restricted I/O-kernel resource use, and cache-aware request scheduling. | Transfer overhead and bandwidth-aware scheduling are established ideas. Its historical SGLang-HiCache baseline must not be equated with our current implementation. |
| [MORI, §§4–6](https://arxiv.org/html/2606.00866) | Relative-idleness placement across GPU/host tiers, sticky placement to limit churn, and program-aware coordination using HiCache. Evaluation replays recorded tool durations by sleeping. | It does not evaluate actual CPU tools competing with transfers during those simulated tool intervals. This leaves a possible empirical question, not proof that an effective controller exists. |
| [MARS, §§4 and 6](https://arxiv.org/html/2604.26963v1) | Tool-boundary telemetry, CPU-pressure-aware admission, request scheduling, and adaptive GPU KV retention; includes OpenHands deployment. | Tool activity as a resource-control signal is already established. Our distinction would need to be direct backup control that yields benefit beyond static policies and resource isolation. |

The papers reviewed do not establish our specific claim that delaying optional GPU→host backups improves real CPU-tool and agent completion time. This is a scoped reading finding, not an exhaustive novelty guarantee.

Two adjacent upstream proposals further limit broad novelty claims: [vLLM back-pressure RFC](https://github.com/vllm-project/vllm/issues/50031) proposes pressure feedback and dropping stores to slow secondary tiers; [programmable KV-cache RFC](https://github.com/vllm-project/vllm/issues/57103) discusses runtime signals, movement policies, and resource arbitration. They are proposals, not evidence that our CPU-tool scheduling mechanism is implemented or effective.

## What our measurements support

| Measurement | Observation | Supported interpretation |
| --- | --- | --- |
| HiCache functionality | GPU→host backups and host→GPU restores observed | Setup is working |
| Reused-prompt repository search | No consistent search slowdown under restore activity | No demonstrated practical effect for this tool/configuration |
| Fresh-prompt repository search | No consistent search slowdown under backup activity | No demonstrated backup interference for this tool/configuration |
| STREAM, five trials per condition | 40.08 GB/s alone; 38.42 GB/s with inference; 37.61 GB/s with inference and backups | A small descriptive HiCache-associated difference, requiring further causal controls |

The STREAM difference is about 2.1% relative to inference without HiCache. Request rates differ, host bookkeeping differs, and block order/shared-node activity can contribute. Range overlap alone does not prove statistical insignificance. Conversely, these observations do not establish that changing transfer timing will improve real tools or end-to-end completion time.

The recorded GPU and CPU affinity both point to NUMA node 0, while the memory policy permits nodes 0–3. Actual host-page placement and DRAM traffic were not measured. We should not attribute the small effect to any particular hardware mechanism yet.

## A feasible narrower research question

**When do write-through, selective write-through, and write-back KV policies improve or degrade multi-turn task completion on a shared GPU/CPU node?**

A bounded study would vary cache reuse, GPU-cache pressure, and tool resource demand while comparing those existing policies. Use repeatable inference→tool→resumption sequences with retained prefixes; measure whole-task completion time, transfer bytes, cache reuse/recomputation, tool time, and inference time. Include a no-tool run to separate cache-policy costs from tool interference.

This is a proposed characterization project, not a claim of unexplored territory or a proven optimization. It preserves the working setup and makes positive, negative, and mixed results useful. A synthetic replay evaluates system behavior; it does not establish agent task-solving accuracy.

## Decision criteria for the original controller

Continue only if representative real-tool runs show reproducible interference, eligible backups actually overlap those tools, and a simple timing policy improves whole-task completion while preserving useful cache reuse. It must beat appropriately tuned static write policies and practical CPU-placement/concurrency controls.

Pivot if the effect remains small/noisy or confined to synthetic stress, or if existing static policies capture the useful benefit. Choose an unrelated topic if characterization does not satisfy the course requirements. Do not claim the original hypothesis has been rejected across all hardware from these limited tests.
