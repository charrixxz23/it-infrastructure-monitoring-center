# Runbook — optional external heartbeat

**Operational history (2026-11-05; not a live status):** during the initial validation, read-only Docker inspection found the six core services, Heartbeat Agent, and Heartbeat Egress Policy healthy; Squid was running. The owner corrected the credential chronology: the original Ping URL remained configured for the three accepted cycles at 16:06:10, 16:07:09, and 16:08:09 UTC. After that validation, the owner created a new provider check, replaced the Git-ignored local secret, and stopped/recreated only `heartbeat-agent`. The owner reports the recreated agent reached `healthy`, followed by four accepted cycles at 17:29:10, 17:30:10, 17:31:10, and 17:32:10 UTC. The owner observed the new check green and receiving automatic pings; the original stopped receiving pings, appeared `Down`, and was later deleted by the owner. No deletion time was supplied. The initial Docker inspection predates the rotation; post-rotation health and provider state are owner-reported, not independently verified here. This runbook did not access the provider or inspect credentials. The supervisor/Windows host remain trusted boundaries; successful operation does not create a host-level egress allowlist.

## Prerequisites and scope

The owner has approved this provider, check, notification method, and outbound policy for the current deployment. The heartbeat monitors HTTP health of Prometheus, Alertmanager, Grafana, and Incident Logger. It does not monitor Node Exporter, Blackbox Exporter, individual targets, alert rules, home-network probes, or the Windows container exporter. A failed home-network probe does not stop the heartbeat if the monitoring control path remains healthy.

Windows, Docker Desktop/WSL2, the agent, and Internet connectivity must be available for regular pings. A complete site/ISP outage prevents the agent from reaching Healthchecks.io; the provider may detect the missing heartbeat later, but notification depends on its independent service and configured notification channel.

## Initial setup and activation

Before handling the Ping URL, review the runtime isolation evidence and threat matrix in `docs/external-heartbeat.md`. The namespace filter is an independent network-layer restriction for Squid's process, but it trusts the policy supervisor, Docker daemon, Windows host, and Docker Desktop/WSL2 backend. A bridge by itself is not a destination allowlist. No host firewall rule was changed; do not add one as part of this runbook because Docker Desktop's backend is shared with unrelated containers. If the required security boundary must withstand a compromised policy supervisor or Docker host, do not activate this design; move to a separately controlled egress gateway/firewall that supports provider FQDNs.

1. The owner created the provider project and check with a 2-minute period, 2-minute grace, and email notification enabled. The owner confirmed receiving loss and recovery emails. Confirm provider-side settings only through the authorized UI; do not record the recipient address or check identifier here.
2. The owner reports that the initial validation still used the original Ping URL. Afterward, the owner created a new check, replaced the ignored local secret, and recreated only the agent; the owner reports accepted cycles from the replacement configuration and later deletion of the original check. Do not copy any Ping URL into this repository, chat, a ticket, shell arguments, screenshots, or source control. The URL grants permission to mark a check successful.
3. In PowerShell at the repository root, create the ignored directory and ensure the ignore rule applies:

   ```powershell
   New-Item -ItemType Directory -Force .secrets | Out-Null
   git check-ignore .secrets/healthchecks-ping-url
   ```

   Review the effective ACL locally before placing the secret. Do not use `/inheritance:r` or replace the ACL wholesale: those operations can remove inherited access required by SYSTEM, Administrators, the file owner, or Docker Desktop's host-side file sharing. If policy requires narrowing access, preserve required administrative/system entries and the account through which Docker Desktop accesses the bind mount, then verify that the mount remains readable by the container. No ACL is changed by this runbook or by this audit.

4. For a new host or an approved rotation, store the current URL through a hidden secure prompt. This example is documentation only. It avoids echoing or placing the URL in command history, but a plaintext string exists briefly in the PowerShell process so it can be written in the format the Linux container reads. Do not print the file. Its ACL inherits from `.secrets`; review it as described above rather than deleting inherited entries:

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

5. Confirm that the owner accepts the residual policy-supervisor and Docker-host risks in the threat matrix. Review the synthetic runtime evidence for the proxy namespace filter and the secret file's Windows ACL. Do not treat a Docker bridge, Squid ACL, or Docker Engine proxy setting as a host-level allowlist.
6. Render configuration without starting services:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml config --quiet
   ```

   This command validates Compose syntax and file existence; it does not contact Healthchecks.io. Verify Docker Desktop is ready and the six core services are healthy before starting the opt-in services.
7. If the opt-in stack is stopped, and the owner authorizes resuming pings, start the agent and its declared dependencies. **Do not run this command when the agent is already healthy:**

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d heartbeat-agent
   docker compose -f compose.yaml -f compose.heartbeat.yaml ps heartbeat-agent
   ```

   Compose starts only the three opt-in services and their declared dependencies; it does not start, stop, or reconfigure the six core services. The agent has no direct uplink and waits for the required internal endpoints and proxy. A healthy cycle sends a real provider ping. Never substitute a synthetic URL for the owner-managed credential.

## Daily operation

All commands below are examples for an operator. This audit did not run start, stop, restart, or failure-injection commands.

Check the nine project containers and the three opt-in services without changing them:

```powershell
docker ps -a --filter label=com.docker.compose.project=it-infrastructure-monitoring-center --format "table {{.Names}}\t{{.Status}}"
docker compose -f compose.yaml -f compose.heartbeat.yaml ps -a heartbeat-agent heartbeat-egress-policy heartbeat-egress-proxy
```

The agent's `healthy` state means its health endpoint observed a recent complete cycle, including acceptance of the success ping. The policy supervisor's `healthy` state confirms its startup firewall/DNS readiness marker; it does not prove the provider is reachable. Squid has no configured Docker healthcheck, so `running` alone is process state. Verify accepted pings and notifications in the authorized provider UI without copying its URL or identifier.

To stop only heartbeat transmissions while leaving its policy/proxy available:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent
```

To stop all three opt-in services without affecting the core stack:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent heartbeat-egress-proxy heartbeat-egress-policy
```

Use `stop`, not `down`; do not use `--remove-orphans`. Starting again uses the activation command above after the cause is understood and the owner authorizes real pings.

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

Check status before reviewing logs. The following commands are read-only:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml ps -a heartbeat-agent heartbeat-egress-policy heartbeat-egress-proxy
docker compose -f compose.yaml -f compose.heartbeat.yaml logs --tail 50 heartbeat-agent
docker compose -f compose.yaml -f compose.heartbeat.yaml logs --tail 50 heartbeat-egress-policy
```

The agent and policy logs are designed to omit URLs, IP addresses, response bodies, and secret values. Still review them locally before sharing any output; never publish raw logs if unexpected content appears. Do not inspect or print the mounted secret file.

- The health endpoint binds only to container loopback and is not published on a host port or reachable by peer containers. Docker's local healthcheck invokes it inside the same container. A healthy state means a recent complete cycle succeeded, including provider acceptance; it is not a target-availability summary.
- Agent logs identify a fixed component and categories such as `dns_error`, `connection_refused`, `connection_error`, `timeout`, or `http_<status>`. If unexpected sensitive content appears, stop sharing the output, preserve access-controlled local evidence, redact it, and rotate the Ping URL.
- DNS/connection errors for a local component: check Docker Desktop health, the local DNS resolver, the four Compose service names, and the monitoring network. Do not include private URLs or addresses in public issues.
- `Healthchecks.io:timeout` or `network_error`: check the policy supervisor health, Squid process state, approved proxy path, and general Internet connectivity. The agent retries on its next cycle; there is no offline queue or direct fallback.
- `unexpected_response` or HTTP status failure: verify the check URL/provider state through the authorized provider UI, not by pasting the URL into commands or logs.
- Missing-heartbeat notifications arrive according to the provider's configured period/grace and delivery channel. Do not infer the outage start or duration from the email timestamp alone.
- After recovery, the next successful cycle resumes pings. Confirm recovery in the authorized provider UI. The owner has confirmed one Down → Up transition and receipt of both emails; exact transition timing and cause were not recorded.

## Future controlled missed-heartbeat test — approval required

The owner-reported Down → Up transition and emails are operational evidence; this repository does not attribute them to a deliberately induced test. Do not test by unplugging the router, disabling the firewall, stopping shared infrastructure, or disrupting another service. If a controlled missed-heartbeat test is needed, obtain explicit approval for a maintenance window, pause only `heartbeat-agent` with the same Compose file list, wait through the configured period/grace, and verify notification and recovery. Record timestamps without the Ping URL or check identifier.

## Rollback

Stop only the opt-in agent, using the same override so Compose identifies the service:

```powershell
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent heartbeat-egress-proxy heartbeat-egress-policy
```

This stops the three opt-in services: agent, proxy, and policy supervisor. Remove the override from future commands; the default stack and its networks remain unchanged. Do not automatically delete the provider check or local secret. If permanently decommissioning the heartbeat, the owner can securely remove the ignored secret and separately disable the provider check through its authorized UI. Do not delete shared monitoring volumes.

## Secret rotation and compromise response

The owner corrected the chronology: the original Ping URL was still in use during the initial validation. The owner later replaced the ignored local secret, created a new check, recreated only `heartbeat-agent`, and confirmed that the original check was subsequently deleted. The owner supplied four accepted post-rotation cycle times; no deletion time was supplied. These provider and lifecycle details are owner-confirmed, not independently queried for this runbook update. Neither secret nor check identifier is recorded here. For a future suspected exposure:

1. Treat the URL as a bearer credential that can forge successful check-ins. In the authorized provider UI, rotate the affected URL; do not remove the old provider check automatically.
2. Stop only `heartbeat-agent` before replacing the ignored local file. Coordinate with the owner because the provider may report a missed heartbeat during rotation; do not stop the core ITMC services.
3. Store the replacement through the hidden secure prompt in the setup section. Preserve SYSTEM, Administrators, owner, and Docker Desktop access in the Windows ACL. Never print the secret or pass it in command arguments.
4. Validate Compose without starting services, then start only the opt-in agent after the owner authorizes real pings. Confirm the agent returns to `healthy` and that the provider accepts a new ping in its authorized UI.
5. For a future rotation, after the replacement is verified, the owner may revoke or retire the affected old check in the provider UI. Do not delete it automatically as part of this runbook.
6. Search only approved local locations for accidental copies. Do not paste matched values into GitHub, CI, chat, reports, or screenshots. Telegram bot/chat credentials are separate and are not needed or accessed by this agent.
