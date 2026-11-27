# Multi-Host Architecture + Migration Plan

**Status**: draft v1 (2026-11-27) — awaiting Phase 0 inventory verification
**Author**: Devin (requested by Themistoklis)
**Scope**: migrate production-critical SocialAuto lanes off the OFFICE
workstation onto always-on infrastructure (existing k3s cluster, OMV, Pi
cluster), keeping OFFICE as a dev/AI workstation.

## 0. Why

OFFICE (Lenovo 82RF, i9-12900H, 32GB, Windows 11 Insider 26H2) runs a
~60-container production fleet inside Docker Desktop's WSL2 VM:

- structural RAM oversubscription — host baseline ~19-20GB + fleet working
  set ~24GB (9GB VM cap + ~15GB swap in use) — see the 2026-11-27 memory
  architecture audit in the `windows-host-ops` skill
- the Docker Desktop x Insider-build interaction crashes the engine several
  times a day (absorbed by the rescue watchdog, but each crash is minutes of
  production downtime)
- a workstation is powered off, rebooted for updates, and thermally throttled
  — none of which a production API should experience

The hardware upgrade (2x32GB DDR5) relieves pressure but does not change the
architectural fact: **production does not belong on a workstation**.

## 1. Current topology (verified 2026-11-27)

| Host | Role today | Facts |
|---|---|---|
| OFFICE (office-1, 100.83.120.93) | everything: full SocialAuto compose fleet, DMR models, browser automation lanes, dev | 32GB RAM, production + dev mixed |
| **Existing k3s cluster** (kube-0, ingress-0, k3s-subnet-router-0, tailscale-operator — tagged tailnet devices) | already hosts EspoCRM + MariaDB, Loki, monitoring lanes | k3s already operational with a Tailscale operator + subnet router — the integration problem is *already solved* there |
| github-omv (100.74.191.58) | NAS-ish always-on host, heavy traffic (19.6GB tx) | OMV base, hosts parts of the workspace stack |
| omv-ha (100.95.117.84) | mail stack (postfix/dovecot relay) | idle most of the time |
| Pi cluster (192.168.1.128, 192.168.1.200) | pi2: nextcloud/collabora/talk workspace + cloudflared-omv; both: go2rtc camera relay | ARM, power-cut fragile (docker networks corrupt on hard power loss — fixed 2026-11-26 with restart policies, but the class remains) |
| galaxy-s22 | phone automation lane | stays as-is |

## 2. Proxmox on Pi — the verdict first

**Proxmox VE does not support ARM.** It is x86-64 only; the ARM ports
(`Proxmox-VE-Port` community builds) are experimental, unsupported, and have
no upgrade path — exactly the wrong foundation for production. On ARM the
modern, production-grade answer is **k3s** (CNCF, ARM-native, etcd/sqlite
backed, runs happily on Pi 4/5 with 8GB), which is also what the existing
cluster already runs — so the org already has operational knowledge, a
Tailscale operator, ingress, and monitoring wired into k3s.

Decision: **no Proxmox on the Pis.** Proxmox enters the picture only if
x86 hardware is added later (e.g. a used mini-PC or the OMV box itself —
OMV and Proxmox are alternatives for the same metal; mixing them is an
anti-pattern).

## 3. Target architecture

```
                       Internet
                          |
                   Cloudflare (tunnels, DNS, WAF)
                          |
        +-----------------+------------------+
        |                                    |
   k3s cluster (always-on)             OFFICE workstation
   - social-api (stateless)            - DMR model serving (GPU)
   - social-frontend                   - ComfyUI / FLUX media gen (GPU)
   - celery beat                       - browser automation lanes
   - workers (default/publishing/      (patchright sidecars, CDP,
     messenger/media-orchestration)      phone-adb bridges)
   - redis, postgres (HA-lite)         - dev / IDE / experiments
   - n8n                               - hot-patch lane stays available
   - metabase, infisical               for iterating on the baked image
   - monitoring (existing loki/prom)
        |
   Pi cluster (ARM, k3s agents or docker)
   - nextcloud/collabora/talk (stays — already there)
   - go2rtc camera relay (stays)
   - optionally: light workers (messenger sweep, digest batching)
```

Lane assignment rationale:

- **k3s**: everything stateless or stateful-with-ordinary-storage that must
  be 24/7 — the API, workers, beat, frontend, redis, postgres, n8n. This is
  the production plane.
- **OFFICE**: everything GPU-bound (DMR inference, ComfyUI rendering) and
  everything browser/phone-bound (stealth automation needs the user's
  residential IP, real display, and USB-attached phone). These lanes are
  *burst* workloads — the machine being a workstation hurts them much less.
- **Pis**: keep what already works there; do not add production load to
  power-cut-fragile ARM boards beyond light, restart-tolerant workers.

Connectivity is already solved: the k3s tailscale operator + subnet router
are live, so cluster services are reachable tailnet-wide; Cloudflare
tunnels already front public ingress (cloudless.gr properties), so moving
the API host changes only the tunnel's backend target, not DNS/TLS/WAF.

## 4. Migration phases

### Phase 0 — inventory + verification gates (1 session)

Before touching anything, verify the assumptions this plan is built on:

- [ ] ssh access to both Pis restored (key-only root auth failed
      2026-11-27 — fix keys or re-provision)
- [ ] actual Pi hardware inventory: model, RAM, SD vs SSD, throttling
      state (`vcgencmd`), power stability (UPS?)
- [ ] existing k3s cluster inventory: which metal hosts kube-0/ingress-0,
      node capacity (cpu/mem free), what namespaces already run,
      storage classes available, is there a second node or single-node
- [ ] github-omv hardware: cpu/ram/disk free — it may be the strongest
      always-on x86 node and a candidate for the k3s control-plane+worker
- [ ] baseline the OFFICE fleet per-container memory profile (docker
      stats capture over 24h — peaks matter, not idle)

Gate: if the k3s cluster is single-node on weak metal, the plan's blast
radius changes — production migration waits for a second x86 node rather
than over-committing ARM.

### Phase 1 — state first (lowest risk, highest value)

Postgres + redis are the lanes whose downtime corrupts everything else
when the workstation crashes, and the lanes with the clearest
always-on-home need:

- [ ] deploy postgres 16 + redis to k3s with persistent volumes,
      automated backups to OMV storage (or R2 — the datalake pattern
      already exists)
- [ ] one-shot logical replication/dump-restore from the OFFICE postgres;
      cut over the API/worker DSNs
- [ ] OFFICE postgres becomes a replica or is retired

### Phase 2 — stateless compute

- [ ] bake the existing ghcr images into k8s manifests/helm (the images
      are already the production artifact — this is a pure orchestration
      port, no code change)
- [ ] social-api + frontend behind the existing ingress + cloudflared
- [ ] celery workers + beat; rabbitmq-free design stays (redis broker)
- [ ] workers keep calling OFFICE-hosted GPU lanes over the tailnet —
      DMR/ComfyUI URLs already are config, not hardcoded
- [ ] cutover = tunnel target switch; rollback = switch back

### Phase 3 — operational hardening

- [ ] monitoring: the existing loki/prometheus lanes extend to the new
      namespaces; alert-architecture (phases 1-3) points at k8s events
- [ ] the workstation guard stack (Host-Guard/DMR-RAM-Guard/rescue
      watchdog) stays — OFFICE still runs GPU lanes — but production API
      no longer dies when the workstation does
- [ ] power-cut hardening for the Pi lanes is already pinned
      (2026-11-26 restart policies); optionally move pi2's workspace
      into k3s as well if the cluster lands on stronger metal

### Phase 4 — optional x86 expansion

If/when x86 hardware joins (a €150-200 used mini-PC outperforms the
whole Pi cluster for production):

- [ ] Proxmox on the x86 node becomes legitimate — or simpler: make it a
      k3s server node and skip the hypervisor layer entirely; k3s HA
      control-plane at 3 nodes
- [ ] at that point the Pi cluster reverts to its natural role:
      camera relay, edge sensors, light workers

## 5. What explicitly does NOT migrate

- DMR model serving + ComfyUI: GPU-bound, stays on OFFICE (the only GPU)
- browser automation sidecars: need residential IP + real session
  fingerprint + the phone-adb lane
- the Windows guard stack: still needed for the GPU/dev lanes
- WhatsApp/phone typing lanes: USB-attached hardware

## 6. Risks + honest caveats

- **Single-node k3s is not HA** — if kube-0 is one machine, production
  gains *location independence* but not *redundancy*. The honest frame:
  this migration trades "dies several times a day" for "dies when one
  always-on box dies, which is rare and planned".
- **Pi cluster is not production metal** — power cuts corrupt docker
  networking (observed 2026-11-26); SD-card I/O is the wrong substrate
  for postgres. The Pis stay in their current role until x86 joins.
- **The tailnet is the control plane** — every cross-host dependency
  (workers -> DMR, api -> postgres) rides Tailscale. Its reliability is
  already proven in this org (multi-GB traffic), but the plan adds more
  critical-path weight to it.
- **State migration is the dangerous step** — postgres cutover needs a
  maintenance window and rehearsed rollback; phases 2+ are trivially
  reversible by comparison.

## 7. Success criteria

- social.cloudless.gr API serves 24/7 through a full OFFICE reboot
- engine crashes on OFFICE produce zero production-visible downtime
  (they only pause media-gen/browser lanes)
- workstation RAM pressure ceases to be a production concern; the 64GB
  upgrade becomes optional rather than structural
