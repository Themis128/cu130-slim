# ig-personal-privacy

Revert `@t_baltzakis` IG to personal + private. The Personal card on Account-type screen resists `input tap` on some builds — try the scripted path first, fall back to manual.

## Scripted path (ADB)

```bash
python .devin/skills/ig-personal-privacy/scripts/switch-to-personal.py
```

Drives: profile → Options → Settings → Account type and tools → scroll → "Switch to personal account" link at bottom → confirm → Account privacy → Private toggle.

## Manual fallback

1. Profile → ☰ → Settings and activity → Account type and tools → **Switch to personal account** (bottom)
2. Settings → Who can see your content → Account privacy → **Private account** → Switch to private

## Note

- `t_baltzakis` is the personal surface; `cloudless.gr` is the brand. Only the personal account needs this.
- If taps don't register on the Personal card, do it by hand — 2 taps.
