# ntfy-phone-ops

Repoint the ntfy Android app to a working server via ADB, end-to-end and
verified. Built 2026-10-29 for the real failure chain on the S22+:

- phone **Tailscale VPN off** → tailnet hosts unreachable,
- subscription bound to a **dead hostname** (`ntfy.tail4ecae1.ts.net`),
- public `ntfy.cloudless.gr` behind **Cloudflare Access** → 302 challenge the
  app can never complete (every `*.cloudless.gr` tunnel hostname is gated;
  `social`/`cloud`/apex are not — see cloudflare-ops "Access blocks
  server-side calls"),
- ntfy server runs `auth_default_access: deny-all` → topics need a user.

## Working endpoint

`http://100.74.191.58:30080` — the ntfy k3s NodePort on `github-omv`, reached
over the tailnet by **IP** (immune to MagicDNS). Requires the phone's
Tailscale VPN connected; the script toggles it via the
`com.tailscale.ipn.CONNECT_VPN` broadcast itself.

## Usage

```bash
adb connect 192.168.1.77:5555            # or USB
NTFY_PHONE_PW=<pw> python .devin/skills/ntfy-phone-ops/scripts/ntfy_set_server.py \
    http://100.74.191.58:30080 --topic cloudless-alerts \
    --user phone --test
```

The script verifies the phone can ping the server (fires CONNECT_VPN if not),
launches the app and **checks it holds foreground before tapping** (never
taps blindly into whatever app is up), sets Default server, deletes stale
subscriptions (long-press → Unsubscribe → Delete permanently), re-adds the
topic via the Add-subscription FAB, fills the "Login required" dialog, and
verifies no "Connection error".

## Server-side ops

ntfy runs in k3s on `github-omv` (ns `ntfy`, svc NodePort `80:30080`,
config `/etc/ntfy/server.yml` with `auth_file` accounts.db). Known users:

- `admin` — rw all
- `cloudless-app` — write-only `cloudless-ops` (stack publishers)
- `kuma` — write-only `cloudless-alerts` (Uptime Kuma alerts)
- `phone` — rw `cloudless-alerts` + `cloudless-ops` (this device)

Rotate the phone credential (non-interactive, `NTFY_PASSWORD` env — the
`--password` flag does NOT exist and hangs on stdin):

```bash
ssh github-omv "sudo k3s kubectl -n ntfy exec -i deploy/ntfy -- \
    env NTFY_PASSWORD=<new> ntfy user change-pass phone"
```

Grant another topic to the phone user:

```bash
ssh github-omv "sudo k3s kubectl -n ntfy exec deploy/ntfy -- \
    ntfy access phone '<topic>' ro"
```

## Also verified working (2026-10-29)

- **LAN endpoint:** `http://192.168.1.200:30080` works too — same NodePort,
  no Tailscale dependency on the phone. Preferred when the phone is on home
  Wi-Fi.
- **Fast path — `ntfy://` deep links:** `am start -a VIEW -d
  'ntfy://<host>/<topic>' io.heckel.ntfy` opens a subscribed topic screen
  directly, skipping the dialog. CAUTION: this build maps `ntfy://` to
  **https** (`ntfys://` is not registered) — only use for TLS endpoints;
  for plain-http servers it produces "Unable to parse TLS packet header".
- **Subscribe dialog validation:** the AutoCompleteTextView server field
  must be *re-typed after checking "Use another server"* for SUBSCRIBE to
  enable (`enabled:false` otherwise — typed-before values don't count).
  Clear with `input keycombination 113 29` (CTRL+A) + `input keyevent 67`.
- **Per-server auth is cached:** once `phone` logs in for a server, later
  subscriptions on the same server skip the Login dialog entirely.
- **Stale-sub delete:** row tap opens topic screen -> 3-dot menu ->
  "Unsubscribe" -> "Delete permanently" (long-press does NOT work).

## Traps learned (do not rediscover)

- Launcher activity is `io.heckel.ntfy/.ui.MainActivity` (not `.MainActivity`)
  — launch via `monkey -p <pkg> -c LAUNCHER 1`.
- `adb shell uiautomator dump` path args get MSYS-mangled → always
  `adb exec-out uiautomator dump /dev/tty`.
- Several ADB transports can attach at once (USB + wireless + offline
  emulator) → the script pins `-s <serial>`.
- "Unsubscribe"/"Add subscription" are `content-desc` icon buttons, not text
  — match both attributes.
- First SUBSCRIBE tap may only dismiss the IME → tap again if the dialog
  persists.
- Public `ntfy.cloudless.gr` is Access-blocked for the app (interactive SSO).
  Don't repoint the phone there — and any server-side publisher using the
  public URL silently fails the same way (publish to `github-omv:30080`).
