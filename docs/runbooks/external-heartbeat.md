# Runbook — optional external heartbeat

**Current state:** disabled. This repository contains no real Healthchecks.io account, check, or Ping URL. Do not treat unit tests or Compose rendering as external validation.

## Prerequisites and scope

Use this only after an owner explicitly approves the provider, check, notification method, and outbound network policy. It monitors HTTP health of Prometheus, Alertmanager, Grafana, and Incident Logger. It does not monitor Node Exporter, Blackbox Exporter, individual targets, alert rules, or the Windows container exporter. A failed home-network probe does not stop the heartbeat if the monitoring control path remains healthy.

Windows, Docker Desktop/WSL2, the agent, and Internet connectivity must be available for regular pings. A complete site/ISP outage prevents the agent from reaching Healthchecks.io; the provider may detect the missing heartbeat later, but notification depends on its independent service and configured notification channel.

## Future setup — do not run during CI or review

1. In a browser, create/authorize a Healthchecks.io account and a new check. Use an expected period and grace interval chosen by the owner; 2 minutes plus 2 minutes is only a starting suggestion. Configure provider-side notifications only after reviewing recipient privacy and delivery behavior.
2. Copy the check's Ping URL without sending it to chat, a ticket, shell command arguments, screenshots, or source control. The URL grants permission to mark that check successful.
3. In PowerShell at the repository root, create the ignored directory and ensure the ignore rule applies:

   ```powershell
   New-Item -ItemType Directory -Force .secrets | Out-Null
   git check-ignore .secrets/healthchecks-ping-url
   $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
   icacls .secrets /inheritance:r /grant:r "${identity}:(OI)(CI)F" | Out-Null
   ```

4. Store the URL through a hidden secure prompt. This example is documentation only and has not been run. It avoids echoing or placing the URL in command history, but a plaintext string exists briefly in the PowerShell process so it can be written in the format the Linux container reads. Apply a user-only ACL and do not print the file:

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
   icacls $secretPath /inheritance:r /grant:r "${identity}:R" | Out-Null
   ```

   Confirm the directory itself is not accessible to unintended local accounts; use your organization's Windows ACL policy if it is stricter. Do not use `Get-Content`, `type`, `cat`, `Write-Host`, verbose shell tracing, or a screenshot to verify secret contents. Rotate the provider URL if its exposure is suspected.

5. Review outbound controls. The Compose bridge permits outbound network access but does not enforce a destination allowlist. Configure and verify an appropriate host firewall/proxy policy for the Docker Desktop path. Do not enable the agent if that path cannot be bounded to approved egress.
6. Render configuration without starting services:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml config --quiet
   ```

   This command validates Compose syntax and file existence; it does not contact Healthchecks.io. Verify Docker Desktop is ready and the four existing services are healthy before activation.
7. Only after the review above, start just the opt-in service without starting dependencies:

   ```powershell
   docker compose -f compose.yaml -f compose.heartbeat.yaml up -d --no-deps heartbeat-agent
   docker compose -f compose.yaml -f compose.heartbeat.yaml ps heartbeat-agent
   ```

   The application sends a real external success ping on a healthy cycle. Do not run this until a real provider check and explicit approval exist. Do not use the synthetic test URL.

## Observe and troubleshoot

- The container health endpoint is internal to its attached Docker networks and is not published on a host port. A healthy state means a recent complete cycle succeeded, including provider acceptance; it is not a target-availability summary.
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
