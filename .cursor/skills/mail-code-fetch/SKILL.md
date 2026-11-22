# mail-code-fetch

Pull the newest OTP/verification code from the self-hosted cloudless.gr mail
server (dovecot on omv-ha) — used for TikTok Business Center email codes,
platform verifications, and any flow where the code lands in mail.

## Use

```bash
python .devin/skills/mail-code-fetch/scripts/fetch-code.py [sender] [subject] [age]
# defaults: fetch-code.py tiktok tiktok 30m  → prints newest 4-8 digit code
```

- Host: `omv-ha` over SSH; query is `doveadm fetch` on `tbaltzakis@cloudless.gr`.
- Scans newest message first; matches `code...<digits>` or `>NNNNNN<` patterns.
- SMS alternative: `adb shell content query --uri content://sms/inbox` (Galaxy S22+).
