$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$policyDockerfile = Join-Path $repoRoot 'services\heartbeat-egress-policy\Dockerfile'
$policyContext = Join-Path $repoRoot 'services\heartbeat-egress-policy'
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$networkName = "itmc-egress-audit-$suffix"
$sinkName = "itmc-egress-audit-sink-$suffix"
$guardName = "itmc-egress-audit-guard-$suffix"
$imageName = "itmc-egress-audit-policy-${suffix}:local"
$imageBuilt = $false
$networkCreated = $false

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        $details = ($output | Out-String).Trim()
        throw "Docker command failed: docker $($Arguments[0]) $($Arguments[1])`n$details"
    }
    return $output
}

$sinkProgram = @'
import select
import socket

listeners = []
for port in (443, 444):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen()
    listeners.append(listener)

ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ipv6.bind(("::", 443))
ipv6.listen()
listeners.append(ipv6)

while True:
    ready, _, _ = select.select(listeners, [], [], 1)
    for listener in ready:
        connection, _ = listener.accept()
        connection.close()
'@

$guardProgram = @'
import egress_policy as policy
import subprocess
import time
policy.validate_provider_addresses = lambda addresses: tuple(sorted(set(addresses)))
policy.PROVIDER_HOST = "approved.invalid"
policy.apply_firewall(policy.build_firewall_commands(("192.0.2.20",)))
policy.write_dnsmasq_files(("192.0.2.20",))
resolver = subprocess.Popen(
    ["dnsmasq", "--keep-in-foreground", f"--conf-file={policy.DNSMASQ_CONFIG}"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    try:
        if policy._query_local_provider() == ("192.0.2.20",):
            break
    except (OSError, policy.PolicyError):
        time.sleep(0.1)
else:
    resolver.terminate()
    raise RuntimeError("synthetic local DNS did not become ready")
time.sleep(600)
'@

$clientProgram = @'
import socket
import struct
import subprocess
import sys

failed = False

def tcp_probe(host, port, expected):
    global failed
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(1)
    try:
        client.connect((host, port))
        allowed = True
    except OSError:
        allowed = False
    finally:
        client.close()
    state = "ALLOWED" if allowed else "BLOCKED"
    print(f"{state}: TCP {host}:{port}")
    failed = failed or (allowed != expected)

tcp_probe("192.0.2.20", 443, True)
tcp_probe("192.0.2.20", 444, False)
tcp_probe("192.0.2.21", 443, False)
tcp_probe("192.0.2.1", 443, False)
tcp_probe("198.51.100.99", 443, False)

def dns_probe(name, expected_ip):
    global failed
    labels = name.split(".")
    qname = b"".join(bytes((len(label),)) + label.encode("ascii") for label in labels) + b"\x00"
    query = struct.pack("!HHHHHH", 43, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
    resolver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    resolver.settimeout(1)
    try:
        resolver.sendto(query, ("127.0.0.1", 53))
        response, _ = resolver.recvfrom(2048)
        answer_count = struct.unpack("!H", response[6:8])[0]
        rcode = response[3] & 0x0F
        if expected_ip is None:
            allowed = rcode != 0 or answer_count == 0
        else:
            allowed = rcode == 0 and answer_count == 1 and socket.inet_aton(expected_ip) in response
    except OSError:
        allowed = False
    finally:
        resolver.close()
    state = ("RESOLVED" if expected_ip is not None else "BLOCKED") if allowed else "UNEXPECTED DNS RESULT"
    print(f"{state}: local DNS {name}")
    failed = failed or not allowed

dns_probe("approved.invalid", "192.0.2.20")
dns_probe("blocked.invalid", None)

result = subprocess.run(["iptables", "-F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
if result.returncode == 0:
    print("UNEXPECTEDLY ALLOWED: proxy UID can modify firewall rules")
    failed = True
else:
    print("BLOCKED: non-root proxy UID cannot modify firewall rules")

ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
ipv6.settimeout(1)
try:
    ipv6.connect(("fd42:192:0:2::20", 443, 0, 0))
    print("UNEXPECTEDLY ALLOWED: IPv6 provider route")
    failed = True
except OSError:
    print("BLOCKED: IPv6 egress to synthetic peer")
finally:
    ipv6.close()

dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
dns.settimeout(1)
qname = b"\x07blocked\x07invalid\x00"
query = struct.pack("!HHHHHH", 42, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
try:
    dns.sendto(query, ("127.0.0.11", 53))
    dns.recvfrom(2048)
    print("UNEXPECTEDLY ALLOWED: direct Docker embedded DNS")
    failed = True
except OSError:
    print("BLOCKED: direct Docker embedded DNS")
finally:
    dns.close()

sys.exit(1 if failed else 0)
'@

try {
    Push-Location $repoRoot
    try {
        & docker build --tag $imageName --file $policyDockerfile $policyContext
        if ($LASTEXITCODE -ne 0) { throw 'Policy test image build failed.' }
        $imageBuilt = $true

        Invoke-Docker -Arguments @(
            'network', 'create', '--driver', 'bridge', '--ipv6',
            '--subnet', '192.0.2.0/24', '--subnet', 'fd42:192:0:2::/64', $networkName
        ) | Out-Null
        $networkCreated = $true

        Invoke-Docker -Arguments @(
            'run', '-d', '--name', $sinkName, '--network', $networkName, '--ip', '192.0.2.20', '--ip6', 'fd42:192:0:2::20',
            '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'NET_BIND_SERVICE',
            '--security-opt', 'no-new-privileges:true', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=1m',
            '--entrypoint', 'python', $imageName, '-c', $sinkProgram
        ) | Out-Null

        Invoke-Docker -Arguments @(
            'run', '-d', '--name', $guardName, '--network', $networkName, '--ip', '192.0.2.10',
            '--cap-drop', 'ALL', '--cap-add', 'NET_ADMIN', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
            '--security-opt', 'no-new-privileges:true',
            '--read-only', '--tmpfs', '/run:rw,noexec,nosuid,size=4m', '--tmpfs', '/tmp:rw,noexec,nosuid,size=1m', '--entrypoint', 'python',
            $imageName, '-c', $guardProgram
        ) | Out-Null

        Start-Sleep -Seconds 2
        $guardState = Invoke-Docker -Arguments @('inspect', '--format', '{{.State.Running}}', $guardName)
        if (($guardState | Select-Object -First 1).Trim() -ne 'True') {
            throw 'Test policy container exited before installing the synthetic filter.'
        }

        $routes = Invoke-Docker -Arguments @('exec', $guardName, 'cat', '/proc/net/route')
        if (-not ($routes | Where-Object { $_ -match '^\S+\s+00000000\s+' })) {
            throw 'The test namespace has no IPv4 default route; egress filtering cannot be exercised.'
        }
        Write-Output 'Verified: the synthetic test namespace has an IPv4 default route.'

        Invoke-Docker -Arguments @(
            'run', '--rm', '--network', "container:$guardName", '--user', '13:13', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--read-only', '--entrypoint', 'python',
            $imageName, '-c', $clientProgram
        )

        $activeHooks = Invoke-Docker -Arguments @('exec', $guardName, 'iptables', '-S', 'OUTPUT')
        $activeRule = $activeHooks | Where-Object { $_ -match '^-A OUTPUT -j ITMC_HE_OUT_[AB]$' } | Select-Object -First 1
        if (-not $activeRule) { throw 'The test egress firewall hook was not active.' }
        $chainName = ($activeRule -split '\s+')[-1]
        $chainRules = Invoke-Docker -Arguments @('exec', $guardName, 'iptables', '-L', $chainName, '-nvx')
        $dropLine = $chainRules | Where-Object { $_ -match '^\s*[1-9][0-9]*\s+[0-9]+\s+DROP\b' } | Select-Object -Last 1
        if (-not $dropLine) { throw 'The synthetic deny probes did not increment the terminal DROP counter.' }

        $ipv6Rules = Invoke-Docker -Arguments @('exec', $guardName, 'ip6tables', '-S', 'OUTPUT')
        $ipv6ActiveRule = $ipv6Rules | Where-Object { $_ -match '^-A OUTPUT -j ITMC_HE6_OUT_[AB]$' } | Select-Object -First 1
        if (-not $ipv6ActiveRule) {
            throw 'The IPv6 deny hook was not installed.'
        }
        $ipv6ChainName = ($ipv6ActiveRule -split '\s+')[-1]
        $ipv6ChainRules = Invoke-Docker -Arguments @('exec', $guardName, 'ip6tables', '-L', $ipv6ChainName, '-nvx')
        $ipv6DropLine = $ipv6ChainRules | Where-Object { $_ -match '^\s*[1-9][0-9]*\s+[0-9]+\s+DROP\b' } | Select-Object -Last 1
        if (-not $ipv6DropLine) { throw 'The synthetic IPv6 deny probe did not increment the terminal DROP counter.' }

        $emergencyResult = Invoke-Docker -Arguments @(
            'exec', $guardName, 'python', '-c',
            'import egress_policy as policy; print(policy._emergency_deny_egress())'
        )
        if (($emergencyResult | Select-Object -Last 1).Trim() -ne 'True') {
            throw 'The namespace emergency deny did not confirm successfully.'
        }
        $closedClientProgram = @'
import socket
import sys
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.settimeout(1)
try:
    client.connect(("192.0.2.20", 443))
except OSError:
    print("BLOCKED: emergency deny closed the synthetic provider route")
    sys.exit(0)
finally:
    client.close()
print("UNEXPECTEDLY ALLOWED: emergency deny left the synthetic provider route open")
sys.exit(1)
'@
        Invoke-Docker -Arguments @(
            'run', '--rm', '--network', "container:$guardName", '--user', '13:13', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--read-only', '--entrypoint', 'python',
            $imageName, '-c', $closedClientProgram
        ) | Out-Null
        Write-Output 'Verified: after emergency deny, the previously allowed route is blocked.'
        Write-Output "PASS: namespace-scoped synthetic egress checks completed on a routed bridge; IPv4/IPv6 terminal DROP counters observed."
    }
    finally {
        Pop-Location
    }
}
finally {
    & docker rm -f $guardName $sinkName 2>$null | Out-Null
    if ($networkCreated) { & docker network rm $networkName 2>$null | Out-Null }
    if ($imageBuilt) { & docker image rm $imageName 2>$null | Out-Null }
}
