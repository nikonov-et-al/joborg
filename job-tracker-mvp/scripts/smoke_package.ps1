param(
    [Parameter(Mandatory = $true)]
    [string]$Artifact
)

$ErrorActionPreference = "Stop"
$Executable = (Resolve-Path -LiteralPath $Artifact).Path
$SmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("jobtracker-smoke-" + [guid]::NewGuid().ToString("N"))
$PreviousDataDir = [Environment]::GetEnvironmentVariable("JOBTRACKER_DATA_DIR", "Process")

try {
    New-Item -ItemType Directory -Path $SmokeRoot | Out-Null
    [Environment]::SetEnvironmentVariable("JOBTRACKER_DATA_DIR", $SmokeRoot, "Process")
    $First = Start-Process -FilePath $Executable -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($First.ExitCode -ne 0) { throw "First packaged smoke run failed: $($First.ExitCode)" }
    Set-Content -LiteralPath (Join-Path $SmokeRoot "preserve.txt") -Value "keep" -NoNewline
    $Second = Start-Process -FilePath $Executable -ArgumentList "--smoke-test" -Wait -PassThru -WindowStyle Hidden
    if ($Second.ExitCode -ne 0) { throw "Second packaged smoke run failed: $($Second.ExitCode)" }
    if (-not (Test-Path -LiteralPath (Join-Path $SmokeRoot "jobtracker.sqlite3"))) {
        throw "Packaged smoke run did not create database"
    }
    if ((Get-Content -Raw -LiteralPath (Join-Path $SmokeRoot "preserve.txt")) -ne "keep") {
        throw "Packaged smoke run changed existing data"
    }
    Write-Output "Packaged smoke test passed."
}
finally {
    [Environment]::SetEnvironmentVariable("JOBTRACKER_DATA_DIR", $PreviousDataDir, "Process")
    if (Test-Path -LiteralPath $SmokeRoot) {
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
    }
}
