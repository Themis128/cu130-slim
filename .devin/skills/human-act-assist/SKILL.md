# human-act-assist — finish the 4 human acts hands-off

One watcher covers the leftover human gates: detects each act completing
and fires the finisher + an ntfy ping to `cloudless-ops`.

    python3 act_assist.py            # status board
    python3 act_assist.py --watch    # poll, auto-finish on detection
    python3 act_assist.py --act x-login

| act | detector | finisher |
|---|---|---|
| x-login | x.com/home tab on CDP | ntfy: OAuth+TOTP armed |
| insta-pass | INSTAGRAM_PASSWORD in .env | ntfy: run session_bootstrap |
| phone-unlock | mWakefulness=Awake | ntfy: meta_totp capture |
| meta-coadmin | manual (no detector) | ntfy: re-check WABA |
