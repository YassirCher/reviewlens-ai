param([int]$TimeoutSeconds = 300, [string]$EnvironmentFile = '')
& (Join-Path $PSScriptRoot 'run-local.ps1') -TimeoutSeconds $TimeoutSeconds -EnvironmentFile $EnvironmentFile
