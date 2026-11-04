# Runbook — optional external heartbeat

**Current state:** disabled. The owner reports that a Healthchecks.io project and check `ITMC - Monitoring Heartbeat` were created manually with a 2-minute period, 2-minute grace time, and email notification. The Ping URL is not stored locally or mounted into Docker. No real ping, missed-heartbeat notification, or recovery test has been validated. Do not treat unit tests or Compose rendering as external validation.

## Prerequisites and scope

Use this only after an owner explicitly approves the provider, check, notification method, and outbound network policy. It monitors HTTP health of Prometheus, Alertmanager, Grafana, and Incident Logger. It does not monitor Node Exporter, Blackbox Exporter, individual targets, alert rules, home-network probes, or the Windows container exporter. A failed home-network probe does not stop the heartbeat if the monitoring control path remains healthy.

Windows, Docker Desktop/WSL2, the agent, and Internet connectivity must be available for regular pings. A complete site/ISP outage prevents the agent from reaching Healthchecks.io; the provider may detect the missing heartbeat later, but notification depends on its independent service and configured notification channel.

## Future setup — do not run during CI or review

Before handling the Ping URL, decide and document the egress policy. The current host audit found no outbound-deny policy for Windows Firewall or WSL Hyper-V and no outbound application-filter rule for Docker Desktop's backend. Docker Engine proxy settings are configured, but that alone does not show that runtime container traffic is forced through an enforcing proxy. The Compose bridge is ordinary NAT egress, not a destination allowlist. Either record explicit owner acceptance of unrestricted destination egress for this personal lab (Option A in the design document), or implement and verify an enforced destination-aware policy (Option B). If neither condition is met, do not copy or store the Ping URL and leave the agent disabled. Do not change host firewall rules as part of this runbook; rules on Docker Desktop's shared backend can affect other containers.

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

5. Confirm that the approved egress policy remains in force and the secret file inherits a reviewed ACL before rendering the override.
6. Render configuration without starting services:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml config --quiet
   ```

   This command validates Compose syntax and file existence; it does not contact Healthchecks.io. Verify Docker Desktop is ready and the four existing services are healthy before activation.
7. Only after provider, ACL, and egress reviews are complete and the owner explicitly approves real pings, start just the opt-in service without starting dependencies. **Do not run this during the current PR review:**

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d --no-deps heartbeat-agent
   docker compose -f compose.yaml -f compose.heartbeat.yaml ps heartbeat-agent
   ```

   The application sends a real external success ping on a healthy cycle. Do not use the synthetic test URL.

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
docker compose -f compose.yaml -f compose.heartbeat.yaml stop heartbeat-agent
```

Remove the override from future commands. The default Compose stack remains unchanged. After stopping the agent, securely delete `.secrets/healthchecks-ping-url` if no longer needed and remove the provider-side check through its authorized UI if the owner wants to disable monitoring entirely. Do not delete shared monitoring volumes.

## Ping URL compromise

Treat the URL as a credential that can forge successful check-ins. In the authorized provider UI, rotate/recreate or delete the exposed check, then stop the agent before replacing the local file. Create a new secret file with the hidden prompt procedure and a restrictive ACL; validate configuration and restart only after network and ownership review. Search for accidental copies in approved local locations without printing matched secret values. Never paste the URL into GitHub, CI, chat, reports, or screenshots. Telegram bot/chat credentials are separate and are neither needed nor accessed by this agent.
