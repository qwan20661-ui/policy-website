param(
    [string]$EnvFile = "metadata\.env",
    [string]$File = "",
    [int]$BatchSize = 0,
    [int]$MaxWorkers = 0,
    [switch]$NoDbImport,
    [switch]$DbDryRun
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$argsList = @("metadata\extractdata.py", "--env-file", $EnvFile)

if ($File) {
    $argsList += @("--file", $File)
}

if ($BatchSize -gt 0) {
    $argsList += @("--batch-size", $BatchSize)
}

if ($MaxWorkers -gt 0) {
    $argsList += @("--max-workers", $MaxWorkers)
}

if ($NoDbImport) {
    $argsList += "--no-db-import"
}

if ($DbDryRun) {
    $argsList += "--db-dry-run"
}

python @argsList
