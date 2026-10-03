"""Execute the Windows launcher with isolated Docker/network adapters."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows/PowerShell launcher verified on its native host")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("scenario,success,expected", [
    ("success", True, ""), ("repeat", True, ""), ("degraded", True, ""),
    ("docker", False, "Docker engine unavailable"), ("environment", False, "Environment file missing"),
    ("missing", False, "OPENROUTER_API_KEY"), ("occupied", False, "Port is occupied"),
    ("migration", False, "migration failure"), ("unhealthy", False, "worker unavailable"),
])
def test_local_launcher_checks_failure_and_repeat_paths(tmp_path, scenario, success, expected):
    environment = tmp_path / ".env"
    environment.write_text("# isolated launcher fixture\n")
    required = ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "NODE_STORAGE_ROOT", "OPENROUTER_API_KEY",
                "YOUTUBE_API_KEY", "SESSION_SECRET", "PUBLIC_TOKEN_HASH_SECRET", "RATE_LIMIT_HASH_SECRET", "ADMIN_EMAIL", "ADMIN_PASSWORD_HASH")
    application = dict.fromkeys(required, "isolated-fixture")
    if scenario == "missing":
        application.pop("OPENROUTER_API_KEY")
    configuration = {"services": {
        "api": {"environment": application, "ports": [{"published": "8000"}]},
        "frontend": {"environment": {"APP_PUBLIC_URL": "http://localhost:3000", "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000"}, "ports": [{"published": "3000"}]},
    }}
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    code = f"""
    . {literal(ROOT / 'scripts/run-local.ps1')}
    $script:scenario = {literal(scenario)}
    $script:configuration = {literal(json.dumps(configuration))}
    $script:calls = [System.Collections.Generic.List[string]]::new()
    function Invoke-LocalDocker {{
      param([string[]]$Arguments)
      $command = $Arguments -join ' '
      $script:calls.Add($command)
      if ($Arguments[0] -eq 'info' -and $script:scenario -eq 'docker') {{ throw 'engine unavailable' }}
      if ($command -match 'config --format json') {{ return $script:configuration }}
      if ($command -match 'ps --format json') {{
        if ($script:scenario -eq 'repeat') {{ return '[{{"Publishers":[{{"PublishedPort":3000}},{{"PublishedPort":8000}}]}}]' }}
        return ''
      }}
      if ($command -match 'up -d' -and $script:scenario -eq 'migration') {{ throw 'injected migration failure' }}
      if ($command -match 'up -d neo4j qdrant' -and $script:scenario -eq 'degraded') {{ throw 'optional projections unavailable' }}
      if ($command -match 'healthcheck worker' -and $script:scenario -eq 'unhealthy') {{ throw 'worker unavailable' }}
      return ''
    }}
    function Test-LocalPort {{ param([int]$Port); return $script:scenario -notin @('occupied', 'repeat') }}
    function Invoke-RestMethod {{
      param($Uri, $TimeoutSec)
      return @{{status = $(if ($script:scenario -eq 'degraded') {{ 'degraded' }} else {{ 'ready' }})}}
    }}
    try {{
      Invoke-ReviewLensLocalStack -EnvironmentFile {literal(environment if scenario != 'environment' else tmp_path / 'absent.env')}
      $result = @{{success=$true; failure=''; calls=@($script:calls)}}
    }} catch {{ $result = @{{success=$false; failure=$_.Exception.Message; calls=@($script:calls)}} }}
    $result | ConvertTo-Json -Compress -Depth 4
    """
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", code],
                            capture_output=True, text=True, timeout=20, check=True)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["success"] is success, data
    assert expected in data["failure"]
    if success:
        assert any("build api worker scheduler migrate frontend" in command for command in data["calls"])
        assert any("run --rm --no-deps worker python -m app.cli validate worker" in command for command in data["calls"])
        assert any("openrouter-catalog-refresh" in command for command in data["calls"])
        assert any("healthcheck scheduler" in command for command in data["calls"])
    else:
        assert "ReviewLens research is ready" not in result.stdout
