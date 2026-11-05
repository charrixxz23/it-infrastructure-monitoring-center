# Runbook — optional external heartbeat

**Current state:** disabled. Synthetic runtime checks verified allowed and denied namespace-filter routes on Docker Desktop/WSL2. The owner separately authorized and executed a limited external DNS + CONNECT + TLS test to `hc-ping.com:443`, which returned `PASS: CONNECT and TLS verified; no HTTP request or Ping URL was sent.` The policy supervisor and proxy were started temporarily and then stopped. The supervisor was initially observed as `starting`; no independent evidence confirms it reached `healthy`. The temporary client used UID/GID 65534, no capabilities, and read-only storage. The heartbeat agent never started and no ping was sent. The owner reports that all six original services remained healthy. The Ping URL is not stored locally or mounted into Docker. Missed-heartbeat notification and recovery remain untested. This evidence does not establish protection from a compromised policy supervisor or Docker host.

## Prerequisites and scope

Use this only after an owner explicitly approves the provider, check, notification method, and outbound network policy. It monitors HTTP health of Prometheus, Alertmanager, Grafana, and Incident Logger. It does not monitor Node Exporter, Blackbox Exporter, individual targets, alert rules, home-network probes, or the Windows container exporter. A failed home-network probe does not stop the heartbeat if the monitoring control path remains healthy.

Windows, Docker Desktop/WSL2, the agent, and Internet connectivity must be available for regular pings. A complete site/ISP outage prevents the agent from reaching Healthchecks.io; the provider may detect the missing heartbeat later, but notification depends on its independent service and configured notification channel.

## Future setup — do not run during CI or review

Before handling the Ping URL, review the runtime isolation evidence and threat matrix in `docs/external-heartbeat.md`. The namespace filter is an independent network-layer restriction for Squid's process, but it trusts the policy supervisor, Docker daemon, Windows host, and Docker Desktop/WSL2 backend. A bridge by itself is not a destination allowlist. No host firewall rule was changed; do not add one as part of this runbook because Docker Desktop's backend is shared with unrelated containers. If the required security boundary must withstand a compromised policy supervisor or Docker host, do not activate this design; move to a separately controlled egress gateway/firewall that supports provider FQDNs.

1. The owner has already created the provider project and check, set to a 2-minute period and 2-minute grace time, with email notification enabled. Reconfirm the check and notification recipient through the authorized provider UI before activation. This repository does not contain the recipient address or check identifier.
2. Copy the check's Ping URL without sending it to chat, a ticket, shell command arguments, screenshots, or source control. The URL grants permission to mark that check successful.
3. In PowerShell at the repository root, create the ignored directory and ensure the ignore rule applies:

   ```powershell
   New-Item -ItemType Directory -Force .secrets | Out-Null
   git check-ignore .secrets/healthchecks-ping-url
   ```

   Review the effective ACL locally before placing the secret. Do not use `/inheritance:r` or replace the ACL wholesale: those operations can remove inherited access required by SYSTEM, Administrators, the file owner, or Docker Desktop's host-side file sharing. If policy requires narrowing access, preserve required administrative/system entries and the account through which Docker Desktop accesses the bind mount, then verify that the mount remains readable by the container. No ACL is changed by this runbook or by this audit.

4. Store the URL through a hidden secure prompt. This example is documentation only and has not been run. It avoids echoing or placing the URL in command history, but a plaintext string exists briefly in the PowerShell process so it can be written in the format the Linux container reads. Do not print the file. Its ACL inherits from `.secrets`; review it as described above rather than deleting inherited entries:

   ```powershell
   $secretPath = Join-Path (Get-Location) '.secrets/healthchecks-ping-url'
   $secureValue = Read-Host 'Paste the Healthchecks Ping URL' -AsSecureString
   $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
   try {
     $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
     [IO.File]::WriteAllText($secretPath, $plainValue.Trim(), [Text.UTF8Encoding]::new($false))
   } finally {
     if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
     Remove-Variable plainValue, secureValue -ErrorAction SilentlyContinue
   }
   ```

   Do not use `Get-Content`, `type`, `cat`, `Write-Host`, verbose shell tracing, or a screenshot to verify secret contents. Rotate the provider URL if its exposure is suspected.

5. Confirm that the owner accepts the residual policy-supervisor and Docker-host risks in the threat matrix. Separately review the synthetic runtime evidence for the proxy namespace filter and the secret file's Windows ACL. Do not treat a Docker bridge, Squid ACL, or Docker Engine proxy setting as a host-level allowlist.
6. Render configuration without starting services:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml config --quiet
   ```

   This command validates Compose syntax and file existence; it does not contact Healthchecks.io. Verify Docker Desktop is ready and the four existing services are healthy before activation.
7. Only after the network boundary, provider configuration, ACL, and secret handling are reviewed and the owner explicitly approves real pings, start the opt-in agent and its declared dependencies. **Do not run this during the current PR review:**

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d heartbeat-agent
   docker compose -f compose.yaml -f compose.heartbeat.yaml ps heartbeat-agent
   ```

   These commands start only the policy supervisor, proxy, and agent dependencies requested by Compose; they do not recreate existing monitoring services. The agent has no direct uplink and waits for required internal endpoints and the proxy. On a healthy cycle it sends a real provider ping. Do not use a synthetic test URL for production activation.

## Limited CONNECT and TLS check — completed once; re-runs require authorization

The owner authorized and completed this controlled diagnostic once. The observed result was `PASS: CONNECT and TLS verified; no HTTP request or Ping URL was sent.` It makes a DNS lookup for the fixed hostname, opens a CONNECT tunnel to `hc-ping.com:443`, and performs a certificate-verified TLS handshake. It does not require, mount, read, or send the Ping URL, and sends no HTTP request or Ping path. Each future run contacts external infrastructure and therefore requires explicit owner authorization.

For a future authorized re-run, ensure the heartbeat sender is not running and the fixed private client address is unused. Build a temporary image with an explicit tag; do not use `docker compose images -q`, which may return no image when the service has no container. Then start only the policy/proxy pair. Building does not start the agent:

```powershell
$probeImage = 'itmc-heartbeat-agent-tls-probe:local'
docker build --tag $probeImage --file services/heartbeat-agent/Dockerfile services/heartbeat-agent
docker compose -f compose.yaml -f compose.heartbeat.yaml up -d heartbeat-egress-proxy
```

Before running the temporary client, inspect the policy service health. Do not infer `healthy` from an initial `starting` state:

```powershell
$policyId = (docker compose -f compose.yaml -f compose.heartbeat.yaml ps -q heartbeat-egress-policy).Trim()
docker inspect --format '{{.State.Health.Status}}' $policyId
```

Run the prepared tool as a non-root temporary client on the private proxy network. It has no capabilities, no secret mount, and no uplink network; Squid sees the source address it already permits:

```powershell
docker run --rm --network itmc-heartbeat-proxy-private --ip 172.31.255.251 --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges:true --read-only --entrypoint python $probeImage /app/provider_tls_probe.py --authorize-provider-contact
```

The tool reports only success or a sanitized error. `PASS` establishes the exact-host CONNECT and TLS handshake at that time; it does not prove future DNS stability or a heartbeat ping. The explicit flag is a per-run guard, not a substitute for owner approval. The temporary client is removed automatically. Afterwards, stop the policy/proxy pair and remove only the explicitly tagged test image:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-egress-proxy heartbeat-egress-policy
docker image rm $probeImage
```

## Observe and troubleshoot

- The health endpoint binds only to container loopback and is not published on a host port or reachable by peer containers. Docker's local healthcheck invokes it inside the same container. A healthy state means a recent complete cycle succeeded, including provider acceptance; it is not a target-availability summary.
- Inspect only sanitized agent logs. They identify a fixed component and an error category; they should not contain the Ping URL. If you suspect otherwise, stop the agent, preserve access-controlled local evidence, redact before sharing, and rotate the Ping URL.
- `component timeout` or `network_error`: check Docker Desktop service health and internal Compose DNS/connectivity for the named component. Do not include private URLs, addresses, or secrets in public issues.
- `Healthchecks.io:timeout` or `network_error`: check the approved outbound firewall/proxy path and general connectivity. The process retries at the next interval; it has no offline queue.
- `unexpected_response` or HTTP status failure: verify the check URL/provider state through the authorized provider UI, not by pasting the URL into commands or logs.
- Missing-heartbeat notifications arrive only after the provider's configured period/grace behavior and its notification delivery. Do not infer exact outage start from notification time alone.
- After recovery, the next successful cycle resumes pings. Confirm recovery in the authorized provider UI. No test alert should be injected during this phase.

## Controlled missed-heartbeat test — future approval required

Do not test by unplugging the router, disabling the firewall, stopping shared infrastructure, or disrupting another service. In a later explicitly approved test window, an owner may pause only `heartbeat-agent` using the same Compose file list, wait through the provider's configured period/grace, and verify the provider notification. Resume the agent and verify recovery. Record timestamps, notification delay, and recovery evidence without including the Ping URL. This test has not been run.

## Rollback

Stop only the opt-in agent, using the same override so Compose identifies the service:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent heartbeat-egress-proxy heartbeat-egress-policy
```

This stops the three opt-in services: agent, proxy, and policy supervisor. Remove the override from future commands; the default stack and its networks remain unchanged. After stopping them, securely delete `.secrets/healthchecks-ping-url` if no longer needed and remove the provider-side check through its authorized UI if the owner wants to disable monitoring entirely. Do not delete shared monitoring volumes.

## Ping URL compromise

Treat the URL as a credential that can forge successful check-ins. In the authorized provider UI, rotate/recreate or delete the exposed check, then stop the agent before replacing the local file. Create a new secret file with the hidden prompt procedure and a restrictive ACL; validate configuration and restart only after network and ownership review. Search for accidental copies in approved local locations without printing matched secret values. Never paste the URL into GitHub, CI, chat, reports, or screenshots. Telegram bot/chat credentials are separate and are neither needed nor accessed by this agent.
