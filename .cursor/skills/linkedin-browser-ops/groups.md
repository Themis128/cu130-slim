# LinkedIn group targeting — the correct groups for cloudless.gr

## The mechanism

Posts target groups via `platform_specific.linkedin.group_urn`
(`urn:li:group:{id}`) — routed through `create_post(container_urn=...)` →
`payload["container"]` on the `/posts` endpoint. **Community Management API
product required** — pending approval (human gate).

## Discovery

`get_member_groups()` enumerates the member's joined groups — run once
CMAPI approves to get the actual urns. Until then, identify groups in the
LinkedIn UI and note their numeric IDs from the group URL
(`linkedin.com/groups/{id}`).

## Target groups (cloudless.gr ICP — SMB owners burned by agencies)

| Group type | Why | Post angle |
|---|---|---|
| Greek SMB / startup communities | Home market, Greek-language trust plays | Fixed scope, EU data residency, 14-day delivery |
| Cloud computing / DevOps / serverless | Technical buyers who feel burned by infra debt | Serverless cost wins, zero-downtime migration |
| Web development / agency-alternatives | Owners actively comparing options | "Burned by agencies" is the exact hook |
| Small business / ecommerce owner groups | Store owners who need the survey/audit funnel | Free audit → fixed-plan pitch |
| AI marketing / martech communities | The AI-marketing service line | Trackable growth, not gut feel |

## Per-group rules

- Groups have post-moderation — promotional copy gets rejected; lead with
  value, not pitch. Survey/audit CTAs land better than "hire us".
- Group posts show to members only — smaller but higher-intent reach vs
  the main feed.
- One post per group per week max — spam-flagged accounts get group-banned.

## Post wiring

```python
post.platform_specific = {
    "linkedin": {"group_urn": "urn:li:group:12345"}
}
```
