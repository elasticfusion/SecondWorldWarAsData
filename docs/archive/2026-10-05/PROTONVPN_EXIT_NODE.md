# Commercial VPN (ProtonVPN) for OpenSERP

Route OpenSERP search traffic through ProtonVPN to avoid datacenter IP blocking. Offers higher availability than Tailscale at the cost of $5/month and slightly lower IP reputation.

**Status:** Planned | **Cost:** ~$5/month | **Last Updated:** 2026-06-15

---

## Why This Over Tailscale

| | Tailscale | ProtonVPN |
|---|---|---|
| Cost | $0 | $5/month |
| IP reputation | Residential (best) | VPN (good) |
| Availability | Home uptime dependent | 99.9% SLA |
| IP rotation | Single IP | 4,000+ servers, 100+ countries |
| Trust | Your machine only | ProtonVPN sees traffic |

Choose ProtonVPN if home uptime is unreliable or you need automatic IP rotation when blocked.

---

## How It Works

```
ECS Task (awsvpc — shared network namespace)
┌──────────────────────────────────────────┐
│  gluetun sidecar                         │
│    HTTP proxy → localhost:8888           │
│    WireGuard tunnel to ProtonVPN         │
│    Control API → localhost:8000          │
│                                          │
│  openserp (Chrome)                       │
│    --proxy-server=http://127.0.0.1:8888  │
└──────────────────────────────────────────┘
           │ WireGuard (UDP)
           ▼
   ProtonVPN server (random from global pool)
           │
           ▼
   Google / Bing / DuckDuckGo
```

[gluetun](https://github.com/qdm12/gluetun) provides a Fargate-compatible HTTP proxy (no `NET_ADMIN` required) that tunnels through WireGuard to ProtonVPN.

---

## Progressive Engine Backoff + IP Rotation

Don't rotate the VPN endpoint immediately — degrade engines first:

```
Level 0: engines=google,bing,duckduckgo    ← start here
         ↓ Google fails (CAPTCHA/empty/429)
Level 1: engines=bing,duckduckgo           ← drop Google
         ↓ Bing fails
Level 2: engines=duckduckgo                ← last resort (most lenient)
         ↓ DuckDuckGo fails
Level 3: ROTATE VPN ENDPOINT              ← all engines blocked on this IP
         ↓ reconnect to new random server
         ↓ restore all engines, return to Level 0
         (4,000+ servers across 100+ countries — inexhaustible)
```

With 4,000+ servers across 100+ countries, exhaustion is effectively impossible.

### Detection Heuristics

| Engine | Blocked signal |
|--------|---------------|
| Google | Empty results + 200, or "unusual traffic" in response body |
| Bing | HTTP 429, or empty results on known-good query |
| DuckDuckGo | HTTP 202 (rate limited), or empty results |

### IP Reputation by Geography

Servers in less-targeted regions (Nordic countries, Eastern Europe, Southeast Asia, Latin America) have far better reputations than US/UK/Netherlands servers. The system rotates across the **full global pool** — no country restriction — naturally favoring pristine IPs.

### Mid-Run Rotation (gluetun control API)

```bash
curl -X PUT http://localhost:8000/v1/vpn/status -d '{"status":"stopped"}'
sleep 3
curl -X PUT http://localhost:8000/v1/vpn/status -d '{"status":"running"}'
# Reconnects to a different random server from cached list. Takes ~10s.
```

---

## Server List Management

No API call per-rotation. gluetun uses a local server list:

1. **Built-in** — baked into the Docker image at build time. Works at startup with no network call.
2. **Periodic refresh** — `UPDATER_PERIOD=480h` updates the list every 20 days through the tunnel.
3. **Image updates** — new gluetun releases include fresh server lists. Track via Dependabot (see below).

---

## Setup

### 1. ProtonVPN Credentials

ProtonVPN Plus ($5/month) → [Downloads](https://account.protonvpn.com/downloads) → WireGuard → generate config.

Store in Secrets Manager:
```bash
aws secretsmanager create-secret \
  --name dev-wwii-pipeline/protonvpn-wg-key \
  --secret-string '{"private_key":"XXXXX","addresses":"10.2.0.2/32"}' \
  --region us-east-1
```

Keys don't expire. Regenerate from dashboard if compromised.

### 2. CloudFormation Sidecar

```yaml
- Name: gluetun
  Image: !Ref GluetunImageUri  # Pin version via Dependabot
  Essential: true
  Environment:
    - {Name: VPN_SERVICE_PROVIDER, Value: protonvpn}
    - {Name: VPN_TYPE, Value: wireguard}
    - {Name: SERVER_COUNTRIES, Value: ""}  # Empty = full global pool
    - {Name: HTTPPROXY, Value: "on"}
    - {Name: HTTPPROXY_LISTENING_ADDRESS, Value: ":8888"}
    - {Name: UPDATER_PERIOD, Value: "480h"}
  Secrets:
    - Name: WIREGUARD_PRIVATE_KEY
      ValueFrom: !Sub "...protonvpn-wg-key:private_key::"
    - Name: WIREGUARD_ADDRESSES
      ValueFrom: !Sub "...protonvpn-wg-key:addresses::"
  HealthCheck:
    Command: [CMD-SHELL, "wget -q -O /dev/null http://localhost:8888 || exit 1"]
    Interval: 15
    Timeout: 5
    Retries: 3
    StartPeriod: 30

- Name: openserp
  DependsOn: [{ContainerName: gluetun, Condition: HEALTHY}]
  Command: ['serve', '--host', '0.0.0.0', '--port', '7001', '--raw', '--proxy', 'http://127.0.0.1:8888']
```

### 3. SearchEngineManager (Python)

```python
class SearchEngineManager:
    def __init__(self, gluetun_url="http://localhost:8000"):
        self.engines = ["google", "bing", "duckduckgo"]
        self.active = list(self.engines)
        self.gluetun_url = gluetun_url

    def get_engines(self) -> str:
        return ",".join(self.active)

    def report_failure(self, engine: str):
        if engine in self.active:
            self.active.remove(engine)
        if not self.active:
            self._rotate_vpn()

    def _rotate_vpn(self):
        requests.put(f"{self.gluetun_url}/v1/vpn/status", json={"status": "stopped"})
        time.sleep(3)
        requests.put(f"{self.gluetun_url}/v1/vpn/status", json={"status": "running"})
        time.sleep(10)
        self.active = list(self.engines)  # restore all engines on fresh IP
```

---

## Image Update Tracking

gluetun is referenced in CloudFormation, not a Dockerfile. Use a thin wrapper for Dependabot:

```dockerfile
# Dockerfile.gluetun
FROM qmcgaw/gluetun:v3.40
```

```yaml
# .github/dependabot.yml
- package-ecosystem: docker
  directory: "/"
  schedule:
    interval: weekly
```

Dependabot PRs on new versions → also refreshes the built-in server list.

---

## Resource Sizing

| Container | CPU | Memory |
|-----------|-----|--------|
| gluetun | 64 | 64 MB |
| openserp | 448 | 960 MB |
| **Total** | 512 | 1024 MB |

Fits existing task definition. No change needed.

---

## Fallback

If gluetun fails:
1. OpenSERP starts anyway (change `DependsOn` to `START`)
2. Bing + DuckDuckGo work from datacenter IPs
3. Google degrades — CloudWatch alarm on health check failure

---

## Open Questions

1. **gluetun HTTP proxy without `NET_ADMIN`** — Confirm proxy-only mode works in Fargate. Test locally first with `--cap-drop=ALL`.
2. **OpenSERP `--proxy` flag** — Verify the image passes this to Chromium.
3. **ProtonVPN IP reputation empirically** — If Google blocks ProtonVPN ranges too, escalate to residential proxy service (~$15/month).

---

## Related

- [TAILSCALE_EXIT_NODE.md](TAILSCALE_EXIT_NODE.md) — Free residential IP approach (preferred if home uptime is reliable)
- [NETWORKING_LIFECYCLE.md](NETWORKING_LIFECYCLE.md) — NAT Gateway management
