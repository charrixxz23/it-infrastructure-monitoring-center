$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$agentContext = Join-Path $repoRoot 'services\heartbeat-agent'
$entrypointPath = (Resolve-Path (Join-Path $agentContext 'heartbeat_entrypoint.py')).Path.Replace('\', '/')
$probePath = (Resolve-Path (Join-Path $PSScriptRoot 'heartbeat_dns_probe.py')).Path.Replace('\', '/')
$suffix = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$imageName = "itmc-heartbeat-dns-audit-${suffix}:local"
$containerName = "itmc-heartbeat-dns-audit-${suffix}"
$resolverPath = Join-Path $env:TEMP "itmc-heartbeat-resolv-${suffix}.conf"

function ConvertTo-WindowsCommandLineArgument {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    $quoted = New-Object System.Text.StringBuilder
    [void]$quoted.Append('"')
    $backslashes = 0

    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') {
            $backslashes++
            continue
        }

        if ($character -eq [char]'"') {
            for ($index = 0; $index -lt (2 * $backslashes + 1); $index++) {
                [void]$quoted.Append('\')
            }
            [void]$quoted.Append('"')
        }
        else {
            for ($index = 0; $index -lt $backslashes; $index++) {
                [void]$quoted.Append('\')
            }
            [void]$quoted.Append($character)
        }
        $backslashes = 0
    }

    for ($index = 0; $index -lt (2 * $backslashes); $index++) {
        [void]$quoted.Append('\')
    }
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $docker = Get-Command docker.exe -CommandType Application -ErrorAction Stop
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $docker.Source
    $startInfo.Arguments = (($Arguments | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument -Value $_
    }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $exitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }

    if ($exitCode -ne 0) {
        $details = @(
            "Docker exited with code ${exitCode}: docker $($Arguments[0..($Arguments.Count - 1)] -join ' ')"
            if (-not [string]::IsNullOrWhiteSpace($stdout)) { "STDOUT:`n$($stdout.TrimEnd())" }
            if (-not [string]::IsNullOrWhiteSpace($stderr)) { "STDERR:`n$($stderr.TrimEnd())" }
        ) -join "`n"
        throw $details
    }

    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        Write-Host $stderr.TrimEnd()
    }
    if (-not [string]::IsNullOrWhiteSpace($stdout)) {
        Write-Output $stdout.TrimEnd()
    }
}

$operationFailure = $null
try {
    Set-Content -LiteralPath $resolverPath -Encoding ascii -NoNewline -Value "nameserver 127.0.0.1`noptions timeout:1 attempts:1 ndots:1`n"

    Invoke-Docker -Arguments @(
        'build', '--tag', $imageName, '--file',
        (Join-Path $agentContext 'Dockerfile'), $agentContext
    ) | Out-Null

    $mounts = @(
        "type=bind,source=$entrypointPath,target=/tmp/heartbeat_entrypoint.py,readonly",
        "type=bind,source=$probePath,target=/tmp/heartbeat_dns_probe.py,readonly",
        "type=bind,source=$($resolverPath.Replace('\', '/')),target=/etc/resolv.conf,readonly"
    )
    $dockerArgs = @(
        'run', '--rm', '--name', $containerName, '--network', 'none',
        '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'NET_BIND_SERVICE',
        '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'KILL',
        '--security-opt', 'no-new-privileges:true', '--read-only',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=4m',
        '--memory', '96m', '--cpus', '0.10', '--pids-limit', '16'
    )
    foreach ($mount in $mounts) { $dockerArgs += @('--mount', $mount) }
    $dockerArgs += @('--entrypoint', 'python', $imageName, '/tmp/heartbeat_dns_probe.py')
    Invoke-Docker -Arguments $dockerArgs
}
catch {
    $operationFailure = $_
}

$cleanupFailures = New-Object System.Collections.Generic.List[string]
try {
    $existingContainers = @(
        Invoke-Docker -Arguments @(
            'ps', '-a', '--filter', "name=$containerName", '--format', '{{.Names}}'
        )
    )
    if ($existingContainers -contains $containerName) {
        Invoke-Docker -Arguments @('rm', '-f', $containerName) | Out-Null
    }
}
catch {
    $cleanupFailures.Add("Could not verify/remove temporary container: $($_.Exception.Message)")
}

try {
    $temporaryImages = @(
        Invoke-Docker -Arguments @('image', 'ls', '--quiet', $imageName)
    )
    if ($temporaryImages.Count -gt 0) {
        Invoke-Docker -Arguments @('image', 'rm', '--force', $imageName) | Out-Null
    }
}
catch {
    $cleanupFailures.Add("Could not verify/remove temporary image: $($_.Exception.Message)")
}

if (Test-Path -LiteralPath $resolverPath) {
    try {
        Remove-Item -LiteralPath $resolverPath -Force -ErrorAction Stop
    }
    catch {
        $cleanupFailures.Add("Could not remove temporary resolver file: $($_.Exception.Message)")
    }
}

if ($cleanupFailures.Count -gt 0) {
    $cleanupMessage = "Cleanup failed:`n - $($cleanupFailures -join "`n - ")"
    if ($operationFailure) {
        throw "Operation failed: $($operationFailure.Exception.Message)`n$cleanupMessage"
    }
    throw $cleanupMessage
}

if ($operationFailure) {
    throw $operationFailure
}
