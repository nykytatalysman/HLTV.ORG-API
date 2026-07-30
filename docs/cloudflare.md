# Cloudflare usage and troubleshooting

HLTV may return a Cloudflare challenge instead of the requested page. This is
not a parser failure: the browser must first receive permission to reach the
page.

For `hltv_service.worker`, a recognized challenge is persisted as blocked raw
evidence, the ingestion run is marked `blocked`, no immediate retry is made,
and existing normalized cache records are retained. The FastAPI process
continues serving that cache with stale-age metadata.

Cloudflare documents that successfully completing a challenge creates a
`cf_clearance` cookie. That clearance is tied to the visitor and device and can
be re-evaluated as browsing behavior changes:

- [Challenge Passage](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/)
- [Clearance cookies](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)

## Optional manual library configuration

Use visible browser mode and a dedicated persistent profile:

```python
from HLTV import HLTVClient

with HLTVClient(
    browser="chrome",
    headless=False,
    profile_dir=".hltv-profile",
    timeout=180,
    min_interval=3,
) as client:
    ranking = client.teams.top_teams(size=5)
```

If a challenge appears, complete it yourself in the browser window. Later runs
using the same profile can reuse valid browser state.

The package deliberately does not solve CAPTCHAs, copy clearance cookies
between machines, rotate proxies, identities or browser fingerprints, or
repeatedly retry blocked requests. Do not point the service at a personal
browser profile.

## Troubleshooting

### The challenge returns on every run

- Confirm that `profile_dir` points to the same dedicated directory.
- Do not open the same profile in two browser processes simultaneously.
- Keep `headless=False`.
- Increase `timeout` so there is time to complete an interactive challenge.
- Reduce request frequency with `min_interval`.

### Headless mode is blocked

Use visible mode. Headless browsers expose different signals and may be
challenged even when the same URL works in a normal browser.

### A previously working profile is challenged again

This can happen when clearance expires or Cloudflare reassesses the session.
Complete the new challenge in the same profile. Do not export or share the
profile because it contains browser state.

### Running in a container or CI

Routine CI should test the parsers with saved, minimal fixtures rather than
scrape the live website. For authorized live monitoring, inject a configured
Selenium driver or a custom fetcher through the public constructor.
