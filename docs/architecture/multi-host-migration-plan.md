# Multi-Host Architecture + Migration Plan

**Status**: DEFERRED by owner decision 2026-11-27 — the hardened workstation
remains the production host for now. Revisit when x86 always-on hardware
joins the fleet. **Hard blocker discovered during review: the current
always-on devices are all ARM SBCs (omv-ha verified aarch64/1GB RAM/SD
boot); the ghcr fleet images are x86-only and cannot run on them at all —
no migration is possible until x86 hardware exists.**
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
| OFFICE (office-1, 100.83.120.93) | everything: full SocialAuto compose fleet, DMR models, browser automation lanes, dev | 32GB RAM x86-64, production + dev mixed — **currently the strongest machine in the fleet** |
| **Existing k3s cluster** (kube-0, ingress-0, k3s-subnet-router-0, tailscale-operator — tagged tailnet devices) | already hosts EspoCRM + MariaDB, Loki, monitoring lanes | k3s already operational with a Tailscale operator + subnet router — the integration problem is *already solved* there |
| github-omv (100.74.191.58) | NAS-ish always-on host, heavy traffic (19.6GB tx) | OMV base, hosts parts of the workspace stack |
| omv-ha (100.95.117.84) | mail stack (postfix/dovecot relay) | **verified 2026-11-27: aarch64 ARM SBC, 4 cores, 954MB RAM, 29GB SD-card boot** — near capacity running mail alone |
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
  **Architecture blocker**: the ghcr fleet images are built x86-64 only —
  they cannot run on ARM Pis/omv-ha at all without a multi-arch rebuild
  (aarch64 variants of every image, untested dependencies, QEMU-speed
  builds). This alone rules out the Pi cluster as a production target
  regardless of capacity.

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

If/when x86 hardware joins — **this is the enabling purchase for the
whole plan** (a €150-250 used mini-PC, ThinkCentre/EliteDesk Tiny class,
i5-8th-gen+, 16-32GB, NVMe, 10-15W idle — outperforms the entire Pi
fleet for production and runs the existing x86 ghcr images unmodified):

- [ ] Proxmox on the x86 node becomes legitimate — or simpler: make it a
      k3s server node and skip the hypervisor layer entirely; k3s HA
      control-plane at 3 nodes
- [ ] at that point the Pi cluster reverts to its natural role:
      camera relay, edge sensors, light workers

### Phase 5 — virtual desktop / Windows VM (future, requires x86)

Once x86 hardware with a hypervisor exists (Phase 4), two VM lanes open
that neither k3s nor ARM can serve:

- **Windows VM** — a small Server/Win11 guest for the lanes that are
  irreversibly Windows-bound if they ever need to leave the workstation:
  nothing today qualifies (the browser lanes need a real display +
  residential fingerprint, which a datacenter VM actively worsens), but
  it future-proofs against OFFICE hardware failure — the rescue path is
  "boot the VM, restore the lane configs", not "buy a laptop same-day".
  Size: 4-8GB RAM, 2-4 vcpu, GPU-less.
- **Virtual desktop VM** — a persistent remote desktop (Windows or
  Linux+xfce) reachable over the tailnet from anywhere, so the user can
  drive browser lanes, IDEs and the phone-adb surface without the
  workstation being physically powered. This is the modern
  desktop-as-infrastructure pattern: the workstation becomes optional
  hardware rather than a single point of presence.
- Honest caveat: neither VM helps the GPU lanes — DMR/ComfyUI need the
  physical GPU. A VM host with GPU passthrough is possible on x86
  (Proxmox + VFIO) but is a deliberate hardware purchase decision, not
  a migration step.

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
