# Tailscale Exit Node for OpenSERP

Route OpenSERP search traffic through a residential IP to avoid Google's datacenter IP blocking.

**Status:** Planned | **Cost:** $0 | **Last Updated:** 2026-06-15

---

## Why

Google aggressively CAPTCHAs/blocks searches from AWS IP ranges. Bing is moderate; DuckDuckGo is lenient. All three are queried on every `/mega/search` call. A residential exit IP eliminates the problem entirely.

---

## How It Works

```
ECS Task (awsvpc — shared network namespace)
┌──────────────────────────────────────────┐
│  tailscale sidecar                       │
│    SOCKS5 proxy → localhost:1055         │
│    WireGuard tunnel to home exit node    │
│                                          │
│  openserp (Chrome)                       │
│    --proxy-server=socks5://127.0.0.1:1055│
└──────────────────────────────────────────┘
           │ WireGuard (UDP)
           ▼
   Home machine (residential ISP IP)
           │
           ▼
   Google / Bing / DuckDuckGo
```

Tailscale runs in **userspace mode** (Fargate has no `NET_ADMIN`). Exposes SOCKS5 on `:1055` and HTTP on `:8080` without kernel tun access.

---

## Setup

### 1. Home Exit Node (one-time)

On an always-on machine (Raspberry Pi, NAS, desktop):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --advertise-exit-node --hostname=home-exit
```

Approve in Tailscale admin or via ACL `autoApprovers`.

### 2. Auth Key

Generate at [admin console → Keys](https://login.tailscale.com/admin/settings/keys):
- Reusable ✓ (works each 0→1 scale)
- Ephemeral ✓ (auto-deregisters on stop)
- Pre-authorized ✓
- Tag: `tag:openserp`

Store:
```bash
aws secretsmanager create-secret \
  --name dev-wwii-pipeline/tailscale-key \
  --secret-string "tskey-auth-XXXXX" --region us-east-1
```

### 3. ACL Policy

```json
{
  "tagOwners": {"tag:openserp": ["autogroup:admin"], "tag:home-exit": ["autogroup:admin"]},
  "acls": [{"action": "accept", "src": ["tag:openserp"], "dst": ["autogroup:internet:*"]}],
  "autoApprovers": {"exitNode": ["tag:home-exit"]}
}
```

### 4. CloudFormation Sidecar

```yaml
- Name: tailscale
  Image: tailscale/tailscale:latest
  Essential: true
  LinuxParameters:
    InitProcessEnabled: true
  Secrets:
    - Name: TS_AUTHKEY
      ValueFrom: !Sub "arn:aws:secretsmanager:${AWS::Region}:${AWS::AccountId}:secret:${EnvironmentName}-wwii-pipeline/tailscale-key"
  Environment:
    - {Name: TS_STATE_DIR, Value: /var/lib/tailscale}
    - {Name: TS_USERSPACE, Value: "true"}
    - {Name: TS_EXTRA_ARGS, Value: "--exit-node=<home-node-tailscale-ip>"}
    - {Name: TS_HOSTNAME, Value: openserp-ecs}
  HealthCheck:
    Command: [CMD-SHELL, "tailscale status --json | grep -q '\"Online\":true' || exit 1"]
    Interval: 15
    Timeout: 5
    Retries: 3
    StartPeriod: 30

- Name: openserp
  DependsOn: [{ContainerName: tailscale, Condition: HEALTHY}]
  Command: ['serve', '--host', '0.0.0.0', '--port', '7001', '--raw', '--proxy', 'socks5://127.0.0.1:1055']
```

---

## Fallback

If the home exit node is offline:
1. Change `DependsOn` condition to `START` (OpenSERP runs without proxy)
2. Bing + DuckDuckGo still work from datacenter IPs
3. Google results degrade — CloudWatch alarm on Tailscale health check failure

---

## Tradeoffs

| Pro | Con |
|-----|-----|
| Free (Tailscale free tier) | Depends on home internet uptime |
| True residential IP (best reputation) | Single IP — if ISP changes it, cached exit-node config breaks |
| No third-party trust | Adds ~20-50ms latency per search |
| Zero bandwidth cost | Home machine must stay on |

---

## Open Questions

1. **OpenSERP `--proxy` flag** — Verify the image passes this to Chromium. May need env var or custom entrypoint.
2. **Resource sizing** — Tailscale uses ~30 MB RAM. Current 512/1024 task should fit.
3. **Key rotation** — Use long-lived reusable key or automate via Tailscale API.

---

## Related

- [PROTONVPN_EXIT_NODE.md](PROTONVPN_EXIT_NODE.md) — Commercial VPN alternative (higher availability, $5/month)
- [NETWORKING_LIFECYCLE.md](NETWORKING_LIFECYCLE.md) — NAT Gateway management
