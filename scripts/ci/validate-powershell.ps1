$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$scriptRoot = Join-Path $repoRoot 'scripts'
$parseErrors = [System.Collections.Generic.List[string]]::new()

Get-ChildItem -Path $scriptRoot -Filter '*.ps1' -File -Recurse | ForEach-Object {
    $scriptFile = $_
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $scriptFile.FullName,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null

    foreach ($parseError in $errors) {
        $relativePath = [System.IO.Path]::GetRelativePath($repoRoot, $scriptFile.FullName)
        $parseErrors.Add("${relativePath}: $($parseError.Message)")
    }
}

if ($parseErrors.Count -gt 0) {
    $parseErrors | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output 'PASS: all PowerShell scripts parse successfully; no script was executed.'
