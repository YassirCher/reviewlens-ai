param([int]$TimeoutSeconds = 300, [string]$EnvironmentFile = '')

function Invoke-LocalDocker {
    param([string[]]$Arguments)
    # Windows PowerShell wraps native stderr as ErrorRecord, including successful
    # Compose progress. Judge Docker by its exit code, while retaining output.
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & docker @Arguments 2>&1
        $commandExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousErrorPreference }
    if ($commandExitCode -ne 0) { throw ('Docker command failed: ' + $Arguments[0] + '. Check the local Docker logs and configuration.') }
    return (($output | ForEach-Object { $_.ToString() }) -join "`n")
}

function Test-LocalPort {
    param([int]$Port)
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $listener.Start(); return $true } catch { return $false } finally { $listener.Stop() }
}

function Invoke-ReviewLensLocalStack {
    param([int]$TimeoutSeconds = 300, [string]$EnvironmentFile = '')
    $ErrorActionPreference = 'Stop'
    if ($TimeoutSeconds -lt 30 -or $TimeoutSeconds -gt 1800) { throw 'TimeoutSeconds must be between 30 and 1800.' }
    $taskRoot = Split-Path -Parent $PSScriptRoot
    if (-not $EnvironmentFile) { $EnvironmentFile = Join-Path $taskRoot '.env' }
    if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) { throw 'Environment file missing. Configure .env using .env.example.' }
    $taskEnv = (Resolve-Path -LiteralPath $EnvironmentFile).Path
    $previousEnvironmentFile = $env:REVIEWLENS_ENV_FILE
    $env:REVIEWLENS_ENV_FILE = $taskEnv
    Push-Location $taskRoot
    try {
        try { $null = Invoke-LocalDocker -Arguments @('info', '--format', '{{.ServerVersion}}') }
        catch { throw 'Docker engine unavailable. Start Docker Desktop before launching ReviewLens.' }
        $compose = @('compose', '--env-file', $taskEnv)
        # Expanded configuration contains credentials: retain it only in memory.
        $configuration = (Invoke-LocalDocker -Arguments ($compose + @('config', '--format', 'json'))) | ConvertFrom-Json
        $application = $configuration.services.api.environment
        $required = @('DATABASE_URL', 'REDIS_URL', 'CELERY_BROKER_URL', 'NODE_STORAGE_ROOT', 'OPENROUTER_API_KEY',
            'YOUTUBE_API_KEY', 'SESSION_SECRET', 'PUBLIC_TOKEN_HASH_SECRET', 'RATE_LIMIT_HASH_SECRET', 'ADMIN_EMAIL', 'ADMIN_PASSWORD_HASH')
        $missing = @($required | Where-Object { -not $application.$_ })
        if ($missing.Count) { throw ('Required configuration missing: ' + ($missing -join ', ')) }
        $site = [uri]$configuration.services.frontend.environment.APP_PUBLIC_URL
        $api = [uri]$configuration.services.frontend.environment.NEXT_PUBLIC_API_BASE_URL
        if ($site.AbsoluteUri.TrimEnd('/') -ne 'http://localhost:3000' -or $api.AbsoluteUri.TrimEnd('/') -ne 'http://localhost:8000' -or
            ($application.BACKEND_CORS_ORIGINS -and 'http://localhost:3000' -notin ($application.BACKEND_CORS_ORIGINS -split ',' | ForEach-Object { $_.Trim() }))) {
            throw 'Local launch requires APP_PUBLIC_URL=http://localhost:3000, NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 and a matching CORS origin.'
        }
        $current = Invoke-LocalDocker -Arguments ($compose + @('ps', '--format', 'json'))
        $containers = @()
        if ($current.Trim()) {
            if ($current.TrimStart().StartsWith('[')) { $containers = @($current | ConvertFrom-Json) }
            else { $containers = @($current -split "`n" | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json }) }
        }
        $ownedPorts = @($containers | ForEach-Object { $_.Publishers } | ForEach-Object { [int]$_.PublishedPort })
        foreach ($service in $configuration.services.PSObject.Properties.Value) {
            foreach ($port in $service.ports) {
                $number = [int]$port.published
                if ($number -gt 0 -and $number -notin $ownedPorts -and -not (Test-LocalPort -Port $number)) {
                    throw ('Port is occupied by another process: ' + $number)
                }
            }
        }
        Write-Host 'Building the local stack and validating server configuration...'
        $null = Invoke-LocalDocker -Arguments ($compose + @('build', 'api', 'worker', 'scheduler', 'migrate', 'frontend'))
        $null = Invoke-LocalDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'worker', 'python', '-m', 'app.cli', 'validate', 'worker'))
        Write-Host 'Starting dependencies, migrations, API, worker, scheduler and frontend...'
        try { $null = Invoke-LocalDocker -Arguments ($compose + @('up', '-d', 'neo4j', 'qdrant')) }
        catch { Write-Warning 'Optional graph/vector projections did not start. Mandatory research readiness will still be checked.' }
        $null = Invoke-LocalDocker -Arguments ($compose + @('up', '-d', '--wait', '--wait-timeout', "$TimeoutSeconds", 'postgres', 'redis', 'migrate', 'api', 'worker', 'scheduler', 'frontend'))
        $null = Invoke-LocalDocker -Arguments ($compose + @('exec', '-T', 'api', 'python', '-m', 'app.cli', 'openrouter-catalog-refresh'))
        $null = Invoke-LocalDocker -Arguments ($compose + @('exec', '-T', 'api', 'python', '-m', 'app.cli', 'analysis-config-seed'))
        foreach ($role in @('api', 'worker', 'scheduler')) {
            $null = Invoke-LocalDocker -Arguments ($compose + @('exec', '-T', $role, 'python', '-m', 'app.cli', 'healthcheck', $role))
        }
        $health = Invoke-RestMethod -Uri 'http://localhost:8000/health/ready' -TimeoutSec 10
        if ($health.status -notin @('ready', 'degraded')) { throw 'The API is live but research readiness has not passed.' }
        if ($health.status -eq 'degraded') { Write-Warning 'Services are ready with degraded optional graph/vector projections.' }
        Write-Host 'ReviewLens services are ready: http://localhost:3000'
    } finally { Pop-Location; $env:REVIEWLENS_ENV_FILE = $previousEnvironmentFile }
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-ReviewLensLocalStack -TimeoutSeconds $TimeoutSeconds -EnvironmentFile $EnvironmentFile }
