# Runbook — optional external heartbeat

**Current state:** disabled. Option B (agent namespace firewall plus a dedicated Squid proxy) is implemented in the draft PR. Isolated runtime checks verified the agent-side deny policy, service-IP refresh, and Squid rejection behavior; they did not verify that Docker Desktop/WSL2 host egress is constrained to the provider. The owner reports that the Healthchecks.io project/check exists with a 2-minute period, 2-minute grace, and email notification. The Ping URL is not stored locally or mounted into Docker. No real ping, missed-heartbeat notification, or recovery test has been validated. Do not activate until the proxy uplink boundary is verified and reviewed.

## Prerequisites and scope

Use this only after an owner explicitly approves the provider, check, notification method, and outbound network policy. It monitors HTTP health of Prometheus, Alertmanager, Grafana, and Incident Logger. It does not monitor Node Exporter, Blackbox Exporter, individual targets, alert rules, home-network probes, or the Windows container exporter. A failed home-network probe does not stop the heartbeat if the monitoring control path remains healthy.

Windows, Docker Desktop/WSL2, the agent, and Internet connectivity must be available for regular pings. A complete site/ISP outage prevents the agent from reaching Healthchecks.io; the provider may detect the missing heartbeat later, but notification depends on its independent service and configured notification channel.

## Future setup — do not run during CI or review

Before handling the Ping URL, review the runtime isolation evidence for option B in `docs/external-heartbeat.md` and the PR. Agent-namespace bypass tests and Squid deny tests passed on a private-only test network, but the host audit found no outbound-deny policy for Windows Firewall or WSL Hyper-V and no outbound application-filter rule for Docker Desktop's backend. Docker Engine proxy settings do not prove enforcement. The agent's two networks are internal and the proxy uplink is separate, but a bridge by itself is not a destination allowlist. Do not activate until an effective restriction of the proxy uplink to the authorized destination is independently verified. Do not change host firewall rules as part of this runbook; rules on Docker Desktop's shared backend can affect other containers.

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

5. Confirm that an independently verified host egress policy restricts the proxy uplink to the authorized provider, and that the secret file inherits a reviewed ACL, before rendering the override. The Compose bridge and the Squid ACL alone do not satisfy this host-side check.
6. Render configuration without starting services:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml config --quiet
   ```

   This command validates Compose syntax and file existence; it does not contact Healthchecks.io. Verify Docker Desktop is ready and the four existing services are healthy before activation.
7. Only after option B's runtime evidence, provider configuration, ACL, and secret handling are reviewed and the owner explicitly approves real pings, start only the two opt-in services. **Do not run this during the current PR review:**

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d --no-deps heartbeat-egress-proxy
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d --no-deps heartbeat-agent
   docker compose -f compose.yaml -f compose.heartbeat.yaml ps heartbeat-agent
   ```

   These commands start no existing monitoring service. The agent has no direct uplink and waits for required internal endpoints and the proxy. On a healthy cycle it sends a real provider ping. Do not use a synthetic test URL for production activation.

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
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent heartbeat-egress-proxy
```

This stops only the two opt-in services. Remove the override from future commands; the default stack and its networks remain unchanged. After stopping both services, securely delete `.secrets/healthchecks-ping-url` if no longer needed and remove the provider-side check through its authorized UI if the owner wants to disable monitoring entirely. Do not delete shared monitoring volumes.

## Ping URL compromise

Treat the URL as a credential that can forge successful check-ins. In the authorized provider UI, rotate/recreate or delete the exposed check, then stop the agent before replacing the local file. Create a new secret file with the hidden prompt procedure and a restrictive ACL; validate configuration and restart only after network and ownership review. Search for accidental copies in approved local locations without printing matched secret values. Never paste the URL into GitHub, CI, chat, reports, or screenshots. Telegram bot/chat credentials are separate and are neither needed nor accessed by this agent.
