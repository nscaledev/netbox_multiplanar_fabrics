# Strategy Position Paper: Automation Architecture for Extremely Large RoCEv2 GPU Fabrics

**Audience:** Network engineering leadership, AI infrastructure/platform engineering, SRE/operations, automation engineering, and data-center deployment teams  
**Scope:** Data-center RoCEv2 Ethernet fabrics supporting extremely large GPU clusters, including fabrics with tens of thousands of GPUs  
**Position:** Large-scale GPU fabrics require a purpose-built AI-fabric operations architecture. Conventional network automation is necessary, but insufficient.

---

## 1. Executive Position

For conventional data-center networks, network automation can often be framed around inventory, configuration rendering, change execution, and post-change validation.

For **large RoCEv2 GPU fabrics**, that framing is too small.

At tens of thousands of GPUs per fabric, the network is no longer just a transport substrate. It becomes a performance-critical component of a distributed training system. Network state, GPU job placement, RDMA congestion behavior, NIC firmware, switch firmware, cabling topology, optical health, and application-level collective performance all interact.

The practical conclusion:

> We should not build a pile of scripts around NetBox, Ansible, or Nornir. We should build an AI-fabric operations platform whose source-of-truth data, topology model, rollout planner, telemetry system, and validation gates understand RoCEv2, GPU jobs, rails, planes, firmware compatibility, and blast radius.

The target architecture should be:

```text
Source of truth / intent
  ↓
AI fabric topology service
  ↓
RoCE policy compiler
  ↓
Config + firmware artifact pipeline
  ↓
Static validation + synthetic validation
  ↓
Topology-aware wave planner
  ↓
Distributed execution workers
  ↓
High-frequency switch/NIC/GPU/job telemetry
  ↓
Health gates, pause/quarantine/rollback
  ↓
Drift reconciliation and lifecycle state update
```

The core thesis:

> For RoCEv2 GPU clusters, the operating unit is not the switch. It is the training fabric slice: GPUs, NICs, rails, paths, jobs, congestion behavior, firmware tuples, and failure domains.

Any automation system that cannot model that will eventually become a very fast outage cannon.

---

## 2. Why RoCEv2 GPU Fabrics Are Different

Ordinary data-center network automation usually focuses on:

- Device inventory
- Interface state
- BGP/EVPN sessions
- VLAN/VRF configuration
- Route counts
- Link health
- Configuration compliance
- Firmware upgrades
- Basic pre/post checks

Large RoCEv2 GPU fabrics require all of that, plus a deeper model of transport behavior and workload impact.

RoCEv2-specific operational concerns include:

- Priority Flow Control behavior
- ECN marking behavior
- Congestion Notification Packet generation
- DCQCN behavior
- Buffer occupancy
- Queue depth
- Per-priority drops
- RDMA retransmits
- QP errors
- NIC firmware
- Switch SDK/firmware
- GPU job placement
- NCCL collective performance
- Rail/plane diversity
- Microburst and incast behavior
- Optical/FEC degradation
- GPU utilization and straggler ranks

In a conventional network, a degraded path may appear as packet loss, latency, or reduced throughput. In a GPU training fabric, a small subset of bad paths can create straggler ranks, slow collectives, stall checkpointing, or collapse training throughput for a large job.

That means the automation system cannot limit itself to “did the switch come back and are the BGP sessions up?” It must answer:

- Which GPU jobs are using this fabric slice?
- Which rails or planes are affected?
- Are we degrading path diversity for a live training job?
- Are PFC/ECN/RDMA counters healthy after the change?
- Did NCCL or other collective performance regress?
- Is the current NIC/switch firmware tuple allowed?
- Is this optic or cable path already marginal?
- Are we about to create correlated risk across a rack, pod, plane, or training partition?

---

## 3. Strategic Requirements

A credible architecture for hyperscale RoCEv2 GPU network operations must satisfy the following requirements.

### 3.1 Model the Fabric Beyond Devices

The data model must understand the GPU fabric as an end-to-end system.

Minimum model scope:

```text
Cluster
  ├── Training partition / tenant / reservation
  ├── Frontend network
  ├── Backend RoCE fabric
  │     ├── Rail / plane
  │     ├── Pod / cell
  │     ├── Rack
  │     ├── GPU host
  │     ├── GPU
  │     ├── RDMA NIC
  │     ├── NIC port
  │     ├── Cable / optic / transceiver
  │     ├── Switch port
  │     ├── Leaf
  │     ├── Spine
  │     └── Super-spine / meta-spine
  └── Workload / job placement
```

The platform must be able to answer:

```text
GPU → NIC → NIC port → cable → switch port → leaf → spine → plane
job → ranks → hosts → GPUs → rails → paths
device → firmware → RoCE profile → compatibility tuple
fabric element → failure domain → blast radius
```

### 3.2 Separate Frontend and Backend Operational Models

Large GPU clusters typically have at least two distinct network planes:

- **Frontend network:** storage, management, logging, checkpointing, service traffic
- **Backend RoCE fabric:** GPU-to-GPU RDMA traffic

The frontend network can often tolerate more conventional automation logic. The backend RoCE fabric cannot.

Backend changes should be gated by GPU/job impact, rail/plane health, RDMA health, and collective-performance validation. The change policy for a storage-facing or management-facing network should not be blindly reused for the backend training fabric.

### 3.3 Treat RoCE Policy as a Compiled Artifact

PFC, ECN, queue, buffer, QoS, DSCP, and MTU policy should not be scattered across Jinja templates as loosely managed snippets.

RoCE policy should be compiled from structured intent.

Inputs:

```text
platform
ASIC
port speed
buffer model
NIC generation
link distance
workload class
fabric tier
validated profile version
```

Outputs:

```text
DSCP / traffic-class mapping
PFC priorities
ECN thresholds
queue and buffer profiles
scheduler / ETS settings
MTU
lossless / lossy class separation
telemetry subscriptions
validation tests
```

The result should be a named, versioned profile:

```text
roce-profile: spectrumx-800g-h100-v3.2
roce-profile: broadcom-400g-h200-v1.7
roce-profile: mixed-nic-gen2-gen3-safe-v2.1
```

These profiles should move through the same promotion lifecycle as software releases:

```text
lab → synthetic → canary → pilot → broad production
```

### 3.4 Make Firmware Compatibility First-Class

In RoCEv2 GPU networks, firmware is part of the protocol surface.

A serious lifecycle model must track compatibility among:

- GPU generation
- NIC model
- NIC firmware
- OFED / driver version
- CUDA / NCCL version
- Switch ASIC
- Switch NOS
- Switch SDK
- Optics type and firmware
- RoCE profile
- Known-good compatibility tuple
- Known-bad compatibility tuple
- Required upgrade order
- Rollback order

The system must be able to express and enforce policies such as:

```text
Do not upgrade switch tier X to NOS Y until NIC firmware Z is deployed.

Do not mix NIC generation A and B in the same training partition unless DCQCN profile C is applied.

Do not enable adaptive routing profile D on rail 2 until all spines in that plane are on SDK E.
```

Tracking “device version” is not enough. The platform must track **compatibility tuples**.

### 3.5 Use Topology-Aware Wave Planning

At this scale, wave planning must be driven by topology and live workload impact, not arbitrary device counts.

A weak plan says:

```text
Upgrade 1,000 switches at a time.
```

A RoCE-aware plan says:

```text
Upgrade no more than:
  - one switch per rack group
  - one device per rail per failure domain
  - N% of any training partition
  - one spine per plane
  - zero paired leaves for active protected jobs
  - zero devices serving canary-excluded workloads
```

The planner should support constraints like:

```text
max_concurrent_by_plane = 1%
max_concurrent_by_pod = 2 devices
max_concurrent_by_job_impact = 0 for protected jobs
max_concurrent_spines_per_plane = 1
min_healthy_paths_per_gpu_pair = threshold
pause_if_pfc_storm_detected = true
pause_if_nccl_throughput_drop_pct > X
pause_if_rdma_retransmits increase > Y
pause_if_ecn_marks exceed learned baseline
```

### 3.6 Use High-Frequency Cross-Layer Telemetry

Five-minute polling is not enough.

The telemetry system must ingest and correlate high-frequency signals from switches, hosts, NICs, GPUs, and workloads.

Switch signals:

```text
port state
link flaps
FEC corrections
symbol errors
queue depth
buffer occupancy
ECN marks
PFC pause frames rx/tx
PFC deadlock detection
drops by queue/priority
WRED/ECN counters
route/ECMP member health
ASIC health events
```

NIC / host signals:

```text
RDMA retransmits
CNPs
ECN reactions
QP errors
link counters
PCIe errors
driver version
firmware version
GPUDirect health
```

GPU / workload signals:

```text
GPU utilization
NCCL all-reduce performance
job step time
straggler rank detection
checkpoint stalls
training throughput
```

The platform should support correlation flows such as:

```text
NCCL job slowed down
  → ranks map to hosts
  → hosts map to NICs
  → NICs map to rail/plane
  → rail maps to leaf/spine paths
  → path maps to ECN/PFC/optics/firmware changes
```

Without this correlation, operations becomes counter archaeology.

### 3.7 Gate Every Rollout Stage

Every rollout should have explicit entry and exit gates.

Entry gate examples:

```text
No active PFC storms
No excessive ECN marking
No elevated RDMA retransmits
No degraded optics on target path
No active protected jobs on impacted fabric slice
All target devices reachable over OOB
All rollback artifacts available
All adjacent redundancy healthy
```

Exit gate examples:

```text
Device back in service
Expected image/config active
BGP/LLDP/route state restored
RoCE QoS profile verified
PFC/ECN counters within baseline
RDMA synthetic tests pass
NCCL canary passes
No job-level regression detected
```

Validation should include more than ping or generic throughput checks.

Recommended canaries:

```text
ib_write_bw / perftest-style RDMA checks
NCCL all_reduce_perf
rail-specific traffic tests
multi-node incast tests
synthetic ECN/PFC behavior checks
```

---

## 4. Target Reference Architecture

The recommended target architecture is shown below.

```text
                         ┌───────────────────────┐
                         │ GPU Cluster Scheduler │
                         │ Slurm/K8s/custom      │
                         └───────────┬───────────┘
                                     │ job placement / protected jobs
                                     ▼
┌──────────────┐     ┌─────────────────────────────┐
│ NetBox/      │────▶│ AI Fabric Topology Service   │
│ Nautobot SoT │     │ rails, planes, jobs, optics  │
└──────┬───────┘     │ NICs, GPUs, failure domains  │
       │             └──────────────┬──────────────┘
       │                            │
       ▼                            ▼
┌──────────────┐     ┌─────────────────────────────┐
│ Config/RoCE  │────▶│ Validation Pipeline          │
│ Compiler     │     │ config, topology, firmware   │
└──────┬───────┘     │ RDMA/NCCL synthetic tests     │
       │             └──────────────┬──────────────┘
       ▼                            ▼
┌──────────────┐     ┌─────────────────────────────┐
│ Artifact     │────▶│ Wave Planner                 │
│ Store        │     │ topology + workload aware    │
└──────────────┘     └──────────────┬──────────────┘
                                    ▼
                         ┌───────────────────────┐
                         │ Execution Workers     │
                         │ CVP/NetQ/gNMI/SSH/API │
                         └───────────┬───────────┘
                                     ▼
                         ┌───────────────────────┐
                         │ Telemetry + Health    │
                         │ switch/NIC/GPU/NCCL   │
                         └───────────┬───────────┘
                                     ▼
                         ┌───────────────────────┐
                         │ Gate/Pause/Rollback   │
                         └───────────────────────┘
```

### 4.1 Source of Truth

The source-of-truth layer should maintain:

- Sites, regions, halls, pods, rows, racks
- Devices and roles
- Interfaces and breakout structure
- Cables, optics, transceivers
- IP addressing
- ASN allocation
- Fabric membership
- Device lifecycle state
- Planned versus active state
- GPU host mapping
- NIC mapping
- Rail/plane mapping
- Maintenance state
- Ownership / reservation metadata

NetBox or Nautobot can serve this role, but either will likely need custom models, plugins, or adjacent services for GPU/RDMA-specific details.

### 4.2 AI Fabric Topology Service

This is the critical missing layer in many conventional designs.

Responsibilities:

- Resolve GPU-to-network path relationships
- Model rails and planes
- Model failure domains
- Map jobs to fabric slices
- Compute blast radius
- Expose topology queries to rollout systems
- Validate cabling symmetry
- Identify correlated risk
- Track firmware compatibility tuples
- Join source-of-truth data with live operational state

Example queries:

```text
Which jobs are impacted if this leaf reloads?

Which GPUs lose rail 3 if this spine plane is degraded?

Are any protected jobs currently using this candidate upgrade wave?

Does this device participate in more than one correlated failure domain?

Is this NIC/switch/NOS/RoCE-profile tuple allowed?
```

### 4.3 Config and RoCE Policy Compiler

Responsibilities:

- Render device configuration
- Compile RoCE profiles
- Generate platform-specific QoS/PFC/ECN/buffer policy
- Generate telemetry subscriptions
- Produce structured diffs
- Emit validation manifests
- Version every generated artifact

Inputs should be structured data, not hand-authored CLI snippets.

Outputs should include:

```text
device config
config diff
RoCE profile ID
firmware dependency manifest
telemetry validation manifest
rollback artifact
```

### 4.4 Validation Pipeline

Validation should occur before and after execution.

Pre-change validation:

- Schema validation
- Config syntax validation
- Platform capability validation
- Firmware compatibility validation
- RoCE profile validation
- Topology validation
- Cabling symmetry validation
- Blast-radius analysis
- Synthetic RDMA test plan generation

Post-change validation:

- Device state verification
- Config compliance
- Routing/control-plane health
- LLDP/cabling consistency
- PFC/ECN counter health
- RDMA health
- NCCL canary health
- Live workload impact analysis

### 4.5 Artifact Store

All generated and executed artifacts should be preserved.

Artifacts:

- Rendered configs
- Config diffs
- RoCE profiles
- Image bundles
- Firmware manifests
- Validation reports
- Pre/post telemetry snapshots
- Job impact reports
- Rollback bundles
- Execution logs

The artifact store is the operational black box recorder. Without it, large-scale operations degrade into myth and shell history.

### 4.6 Wave Planner

The wave planner owns rollout sequencing and blast-radius control.

Inputs:

- Candidate device set
- Topology graph
- Live workload state
- Health state
- Maintenance windows
- Platform constraints
- Firmware dependencies
- Risk policy
- Operator override policy

Outputs:

- Ordered wave plan
- Per-wave blast radius
- Pre-check plan
- Execution plan
- Rollback plan
- Pause criteria
- Success criteria

### 4.7 Execution Workers

Execution workers should be boring and bounded.

They should:

- Execute one well-defined task
- Return structured results
- Avoid owning orchestration logic
- Support retry with idempotence
- Emit logs and metrics
- Never independently expand blast radius

Execution mechanisms may include:

- CloudVision API
- NetQ / Cumulus APIs
- gNMI
- NETCONF
- REST APIs
- Nornir
- pyGNMI
- NAPALM
- SSH/CLI as fallback

The key architecture rule:

> Orchestration belongs in the orchestrator. Device interaction belongs in workers.

### 4.8 Telemetry and Health Gate Layer

The health gate decides whether rollout proceeds, pauses, quarantines, or rolls back.

It must consume:

- Switch telemetry
- NIC telemetry
- GPU telemetry
- Scheduler/job state
- Synthetic test results
- Config compliance state
- Firmware state
- Historical baselines

Health decisions should be explicit and explainable.

Example:

```text
Wave 7 paused:
  - RDMA retransmits increased 4.2x on rail 2
  - NCCL all_reduce canary degraded 18%
  - ECN marks exceeded baseline by 3.1σ
  - Impact isolated to pod p17, spine plane s2
  - Candidate rollback available
```

---

## 5. Platform and Framework Options

There is no single universal “hyperscale RoCEv2 automation framework.” There are platform families and architectural patterns.

### 5.1 NVIDIA Spectrum-X / Cumulus / NetQ-Oriented Stack

Likely fit:

- NVIDIA-heavy GPU clusters
- Spectrum Ethernet switches
- BlueField / ConnectX / SuperNIC environments
- Operators that want a vertically integrated Ethernet AI fabric stack

Possible stack:

```text
NVIDIA Spectrum-X
NVIDIA Cumulus Linux
NVIDIA NetQ
BlueField / ConnectX / SuperNIC telemetry
gNMI / OpenTelemetry export
Custom orchestration around SoT and scheduler
```

Strengths:

- Strong alignment with NVIDIA GPU/NIC/switch ecosystem
- RoCEv2-specific operational focus
- Better chance of integrated telemetry across switch/NIC/GPU boundaries
- Relevant for AI factory-style operations

Risks:

- Vendor ecosystem coupling
- Need to verify API depth and operational control at fleet scale
- Still requires custom integration with source of truth, scheduler, and rollout policy

### 5.2 Arista EOS / CloudVision-Oriented Stack

Likely fit:

- Arista EOS-heavy environments
- Operators already standardized on CloudVision
- Strong need for structured config, change control, image management, and compliance

Possible stack:

```text
Arista EOS
CloudVision
Arista streaming telemetry
Custom RoCE profile compiler
Custom job-aware rollout controller
NCCL/RDMA validation harness
```

Strengths:

- Mature network lifecycle platform
- Strong image/config/change-control workflows
- Good operational fit for large Ethernet fabrics

Risks:

- CloudVision is a network operations platform, not a complete GPU-fabric brain
- Need additional AI/RDMA/job-awareness layer
- Need explicit RoCE policy and compatibility modeling outside basic config management

### 5.3 SONiC / SAI-Based Open Stack

Likely fit:

- Organizations building hyperscaler-style open network platforms
- Need for vendor flexibility and internal control
- Strong internal engineering capability

Possible stack:

```text
SONiC
SAI/ASIC abstraction
custom source of truth
custom fabric topology service
custom rollout controller
custom telemetry lake
custom RDMA/NCCL health engine
```

Strengths:

- Control and flexibility
- Hardware/vendor optionality
- Fits organizations that want cloud-provider-style infrastructure ownership

Risks:

- Heavy engineering burden
- Operational maturity must be built, not bought
- RoCEv2 behavior depends deeply on ASIC/NIC/platform specifics
- Plain SONiC is not enough by itself

### 5.4 FBOSS-Like Hyperscaler Control Plane

Likely fit:

- True hyperscaler or near-hyperscaler operators
- Organizations willing to build and own the full network control plane
- Large internal platform engineering teams

Possible stack:

```text
Open or semi-open switch control plane
Custom controllers
Custom telemetry pipeline
Custom scheduler/fabric correlation
Custom lifecycle and rollout systems
```

Strengths:

- Maximum control
- Deep integration with internal scheduler, telemetry, and infrastructure systems
- Best fit for unique scale and operating model

Risks:

- Very high engineering cost
- Requires long-term platform ownership
- Not appropriate for small teams without strong platform investment

### 5.5 NetBox/Nautobot + Composable Automation Stack

Likely fit:

- Small to mid-sized teams scaling aggressively
- Organizations that need pragmatic control without building a full FBOSS-style stack
- Environments with mixed vendor APIs and custom workflow requirements

Possible stack:

```text
NetBox or Nautobot
Git
Jinja2 / structured config compiler
Nornir
pyGNMI / gNMIc
NAPALM / Netmiko where necessary
Temporal / Argo Workflows / Kubernetes Jobs / Celery
Prometheus / VictoriaMetrics / ClickHouse / OpenTelemetry
NCCL and RDMA synthetic test harnesses
Batfish / Containerlab where useful
```

Strengths:

- Flexible and composable
- Good fit for automation-heavy network engineering teams
- Allows incremental adoption
- Can integrate with vendor platforms instead of replacing them

Risks:

- Easy to under-design orchestration
- Easy to overuse SSH/CLI execution
- Requires disciplined data modeling and artifact management
- Requires custom GPU/RDMA-aware topology and health logic

---

## 6. Recommended Operating Model

### 6.1 Change Classes

Define distinct change classes with different gates.

Suggested classes:

```text
Class 0: Metadata-only source-of-truth updates
Class 1: Non-disruptive config changes
Class 2: Risky but reversible network config changes
Class 3: RoCE policy changes
Class 4: Switch firmware/NOS upgrades
Class 5: NIC/host firmware or driver changes
Class 6: Cross-layer compatibility tuple changes
Class 7: Emergency remediation
```

RoCE policy and firmware changes should have stricter gates than ordinary config changes.

### 6.2 Rollout Lifecycle

Recommended lifecycle:

```text
1. Propose change
2. Render intended artifacts
3. Validate statically
4. Compute blast radius
5. Generate wave plan
6. Run preflight health gate
7. Execute lab/synthetic validation
8. Execute production canary
9. Evaluate telemetry and workload impact
10. Expand through topology-aware waves
11. Pause/quarantine/rollback on gate failure
12. Finalize state and publish report
```

### 6.3 Firmware Wave Example

A firmware wave should behave roughly as follows:

```text
1. Select candidate devices.
2. Exclude devices serving protected jobs.
3. Exclude devices whose adjacent redundancy is degraded.
4. Compute rail/plane/pod blast radius.
5. Drain or cordon impacted GPU hosts if needed.
6. Run RDMA/NCCL baseline canary.
7. Upgrade tiny canary set.
8. Re-run synthetic and live-job telemetry gates.
9. Expand wave geometrically if clean.
10. Pause automatically on PFC storm, ECN anomaly, RDMA retransmit spike, or NCCL regression.
11. Roll back or quarantine bad devices.
12. Write final state back to SoT and artifact history.
```

### 6.4 Configuration Rollout Example

A RoCE config rollout should behave similarly:

```text
1. Identify affected fabric slice.
2. Compile RoCE profile and device configs.
3. Validate profile against platform capability matrix.
4. Validate DSCP/TC/PFC/ECN/MTU consistency end-to-end.
5. Compute affected jobs, rails, planes, and paths.
6. Run baseline telemetry and synthetic RDMA checks.
7. Apply to canary devices.
8. Validate PFC/ECN/RDMA/NCCL behavior.
9. Expand by topology-aware waves.
10. Pause on anomaly.
11. Reconcile intended and observed state.
```

---

## 7. Validation Requirements

### 7.1 Configuration Validation

Required checks:

```text
No PFC enabled on unintended priority
No lossy/lossless class overlap
ECN thresholds match validated profile
MTU consistent host-to-host
DSCP/TC mapping consistent end-to-end
All RDMA NIC ports have symmetric fabric attachment
No two rails accidentally share a correlated failure domain
No missing ECMP member for a rail
No host connected to wrong plane
No mixed firmware tuple outside allowed matrix
No cabling asymmetry that breaks collective assumptions
```

### 7.2 Topology Validation

Required checks:

```text
Every GPU has expected rail count
Every NIC port maps to expected plane
Every host has expected leaf diversity
Every rack has expected plane diversity
Every leaf/spine relationship matches design
Every optic/cable path matches expected media policy
Every training partition has expected path redundancy
No accidental correlation across rails
```

### 7.3 Telemetry Validation

Required checks:

```text
PFC pause frames within baseline
No PFC storm or deadlock signal
ECN marks within expected range
RDMA retransmits within baseline
CNP rate within expected range
No unexpected queue drops
No excessive FEC/symbol errors
No abnormal queue depth
No route/ECMP member loss
No NCCL canary degradation
No protected workload regression
```

### 7.4 Synthetic Workload Validation

Recommended tests:

```text
single-host NIC loop sanity
host-to-host RDMA bandwidth
rail-specific RDMA test
multi-node incast pattern
NCCL all_reduce_perf
NCCL all_gather_perf
NCCL reduce_scatter_perf
job step-time canary
```

---

## 8. Source of Truth Position

NetBox or Nautobot can be the system of record, but they should not be treated as the entire automation platform.

Recommended division of responsibility:

```text
NetBox/Nautobot:
  - inventory
  - cabling
  - IPAM
  - device roles
  - platform metadata
  - lifecycle metadata
  - custom models/plugins for GPU fabric structure

External services:
  - topology graph
  - RoCE policy compilation
  - firmware compatibility matrix
  - wave planning
  - execution orchestration
  - telemetry correlation
  - health gates
  - artifact management
```

Nautobot may be attractive where the organization wants an automation-oriented source-of-truth platform with jobs and an ecosystem for golden config, SSoT, lifecycle management, and Nornir integration.

NetBox may be attractive where the organization wants a more focused inventory/IPAM/DCIM source of truth with automation built around its API.

Either can work. Neither should be the whole animal.

---

## 9. Anti-Patterns to Avoid

Avoid these patterns:

```text
NetBox → Jinja → SSH push loop
```

```text
One giant Ansible playbook that upgrades a fabric
```

```text
Device-count-based upgrade waves
```

```text
Manual spreadsheet wave planning
```

```text
RoCE QoS settings scattered through templates with no profile versioning
```

```text
Firmware tracked as independent device facts rather than compatibility tuples
```

```text
Validation that stops at BGP session health
```

```text
Five-minute polling used as the main health signal
```

```text
No job scheduler integration
```

```text
No artifact history for rendered config, diffs, validation, and rollback
```

```text
No quarantine state for suspect devices, optics, or firmware tuples
```

These patterns may work for hundreds of switches. They become dangerous at tens of thousands of GPUs.

---

## 10. Recommended Initial Roadmap

### Phase 1: Data and Topology Foundation

Deliverables:

- Confirm source-of-truth ownership
- Model GPU hosts, NICs, rails, planes, and switch ports
- Model cabling and optics
- Model failure domains
- Build topology-query API
- Define firmware compatibility tuple schema
- Define RoCE profile schema

Success criteria:

- Can answer “which GPUs/jobs/rails are impacted by this device?”
- Can answer “which devices are part of this training partition?”
- Can answer “is this firmware/profile tuple allowed?”

### Phase 2: Artifact and Validation Pipeline

Deliverables:

- Config rendering pipeline
- RoCE profile compiler
- Config diff generation
- Static validation checks
- Firmware compatibility validation
- Artifact store
- Pre/post validation report format

Success criteria:

- Every change produces versioned artifacts
- Every rendered config can be traced back to intent inputs
- Every RoCE profile is named, versioned, and validated

### Phase 3: Telemetry Correlation

Deliverables:

- Switch telemetry ingestion
- NIC telemetry ingestion
- GPU/job telemetry ingestion
- Historical baselines
- Health scoring
- Anomaly detection hooks
- Correlation service

Success criteria:

- Can correlate job degradation to fabric paths
- Can detect PFC/ECN/RDMA anomalies during rollout
- Can determine whether a fabric slice is safe for maintenance

### Phase 4: Wave Planner and Execution

Deliverables:

- Topology-aware wave planner
- Scheduler/job-state integration
- Worker execution framework
- Health gates
- Pause/quarantine/rollback logic
- Operator approval workflow

Success criteria:

- Can safely execute canary rollouts
- Can expand waves under explicit policy
- Can automatically pause on telemetry regression
- Can produce auditable rollout reports

### Phase 5: Closed-Loop Operations

Deliverables:

- Drift detection
- Auto-remediation for low-risk drift
- Quarantine workflows
- Compatibility tuple promotion
- Long-term reliability analytics
- Fleet health dashboards

Success criteria:

- Operators can manage by intent and exception
- Known-bad devices/optics/tuples are isolated quickly
- Fleet state converges back toward intended state

---

## 11. Recommended Engineering Principles

### Principle 1: The topology graph is a production dependency

If the topology model is wrong, the automation system will make unsafe decisions. Treat topology data quality like software quality.

### Principle 2: RoCE policy must be versioned and promoted

PFC/ECN/buffer/QoS settings should move through environments like code.

### Principle 3: Firmware is not a scalar

A device version is not enough. The compatibility tuple is the operational unit.

### Principle 4: Wave planning must be topology-aware

Device count is not blast radius.

### Principle 5: Validation must be workload-aware

BGP up does not mean the fabric is healthy for distributed training.

### Principle 6: Telemetry must be cross-layer

Switch counters without NIC, GPU, and job context are necessary but incomplete.

### Principle 7: Execution workers should be dumb

Workers execute bounded tasks. Orchestrators own state, gates, retries, pause, rollback, and blast radius.

### Principle 8: Every change should produce artifacts

If the system cannot later explain what it intended, what it changed, what it observed, and why it proceeded, it is not mature enough.

---

## 12. Bottom-Line Recommendation

For extremely large RoCEv2 GPU fabrics, the organization should build or adopt an **AI-fabric operations platform**, not merely a network automation framework.

The minimum viable architecture is:

```text
Intent-driven topology model
+
Validated RoCE policy compiler
+
Firmware compatibility matrix
+
Topology-aware wave planner
+
Distributed execution
+
High-frequency switch/NIC/GPU telemetry
+
NCCL/job-aware health gates
+
Automatic pause/quarantine/rollback
```

Platform choice depends on vendor environment:

```text
NVIDIA-heavy:
  Spectrum-X + Cumulus + NetQ + custom AI-fabric orchestration

Arista-heavy:
  EOS + CloudVision + custom RoCE/job-aware orchestration layer

Open/hyperscaler-style:
  SONiC/FBOSS-like control plane + custom topology, telemetry, and rollout platform

Composable enterprise build:
  NetBox/Nautobot + Git + compiler + Nornir/gNMI + Temporal/Argo + telemetry lake
```

The strategic position should be explicit:

> We will use source-of-truth and automation tooling, but we will not pretend that generic network automation is sufficient for RoCEv2 GPU fabrics. Our platform must understand the fabric as part of the AI training system.

That is the difference between scaling operations and scaling risk.

---

## Appendix A: Example Rollout Gate Contract

```yaml
gate_name: roce_fabric_wave_gate
scope:
  fabric: backend-roce
  cluster: gpu-cluster-a
  wave_id: wave-007

entry_conditions:
  max_pfc_pause_rate_multiplier: 1.5
  max_rdma_retransmit_rate_multiplier: 1.2
  max_ecn_mark_rate_multiplier: 2.0
  require_oob_reachability: true
  require_adjacent_redundancy_healthy: true
  protected_jobs_allowed: false
  require_rollback_artifact: true

exit_conditions:
  require_device_reachable: true
  require_expected_image: true
  require_expected_config: true
  require_bgp_restored: true
  require_lldp_expected: true
  require_roce_profile_verified: true
  max_nccl_canary_degradation_pct: 5
  max_rdma_retransmit_rate_multiplier: 1.2
  max_ecn_mark_rate_multiplier: 2.0

pause_conditions:
  pfc_storm_detected: true
  nccl_degradation_pct_gt: 10
  rdma_retransmit_multiplier_gt: 2.0
  ecn_mark_multiplier_gt: 4.0
  unexpected_job_failure: true
```

---

## Appendix B: Example RoCE Profile Object

```yaml
profile_id: roce-profile-spectrumx-800g-h100-v3.2
description: Validated backend RoCE profile for 800G Spectrum-X / H100 cluster
platforms:
  switches:
    - vendor: nvidia
      nos: cumulus-linux
      asic: spectrum
  nics:
    - model: connectx
      generation: cx7
      firmware_min: 28.x
      firmware_max: 29.x

traffic_classes:
  rdma:
    dscp: [26]
    traffic_class: 3
    pfc_enabled: true
    ecn_enabled: true
  control:
    dscp: [48]
    traffic_class: 7
    pfc_enabled: false
    ecn_enabled: false
  default:
    dscp: [0]
    traffic_class: 0
    pfc_enabled: false
    ecn_enabled: false

validation:
  required_tests:
    - pfc_priority_consistency
    - ecn_threshold_consistency
    - mtu_end_to_end
    - rdma_bandwidth_canary
    - nccl_all_reduce_canary

promotion_state: production
```

---

## Appendix C: Example Topology Queries

```text
GET /topology/impact?device=leaf-1234

GET /topology/jobs?rail=rail-3&pod=pod-17

GET /topology/paths?gpu=gpu-host-99/gpu-3

GET /topology/compatibility?device=leaf-1234&target_image=nos-5.12

GET /topology/wave-plan?change=upgrade-nos&candidate_set=fabric-a-spines
```

---

## Appendix D: Glossary

**Backend fabric**  
The GPU-to-GPU network used for distributed training communication, usually separate from frontend/service traffic.

**CNP**  
Congestion Notification Packet. Used in RoCEv2 congestion-control behavior.

**DCQCN**  
Data Center Quantized Congestion Notification. A congestion-control mechanism used for RoCE environments.

**ECN**  
Explicit Congestion Notification. Switches mark packets to signal congestion instead of dropping them.

**NCCL**  
NVIDIA Collective Communications Library. Commonly used for GPU collective operations such as all-reduce.

**PFC**  
Priority Flow Control. Ethernet flow-control mechanism used to create lossless behavior for selected traffic priorities.

**Rail / plane**  
A logical or physical slice of the GPU backend fabric, often used to provide path diversity and parallel bandwidth.

**RoCEv2**  
RDMA over Converged Ethernet version 2. Allows RDMA over routable Ethernet/IP networks.

**Training fabric slice**  
The set of GPUs, NICs, switch ports, paths, rails, and jobs affected by a particular operational change.

