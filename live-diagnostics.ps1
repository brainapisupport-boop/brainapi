param(
    [string]$BaseUrl = "https://api.brainapi.site",
    [string]$EnvPath = ".env",
    [string]$ReportPath = ".\live-diagnostics-report.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    $line = Get-Content $Path | Where-Object { $_ -match "^\s*$Key\s*=\s*" } | Select-Object -First 1
    if (-not $line) {
        return ""
    }

    return ($line -replace "^\s*$Key\s*=\s*", "").Trim().Trim('"').Trim("'")
}

function Invoke-Status {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action,
        [int[]]$Ok = @(200)
    )

    try {
        $codeText = & $Action
        $code = [int]($codeText.Trim())
        $passed = $Ok -contains $code
        $state = if ($passed) { "PASS" } else { "FAIL" }
        Write-Host ("[$state] $Name => HTTP $code")
        return [PSCustomObject]@{ name = $Name; passed = $passed; code = $code }
    }
    catch {
        Write-Host ("[FAIL] $Name => $($_.Exception.Message)")
        return [PSCustomObject]@{ name = $Name; passed = $false; code = $null }
    }
}

$apiKeysRaw = Get-EnvValue -Key "API_KEYS" -Path $EnvPath
$apiKey = ""
if ($apiKeysRaw) {
    $apiKey = ($apiKeysRaw -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)
}

$adminKey = Get-EnvValue -Key "ADMIN_API_KEY" -Path $EnvPath

$results = @()
$results += Invoke-Status -Name "GET /health" -Action { curl.exe --max-time 20 -sS -o NUL -w "%{http_code}" "$BaseUrl/health" }
$results += Invoke-Status -Name "GET /api/v1/metrics" -Action { curl.exe --max-time 20 -sS -o NUL -w "%{http_code}" "$BaseUrl/api/v1/metrics" }
$results += Invoke-Status -Name "GET /google verification" -Action { curl.exe --max-time 20 -sS -o NUL -w "%{http_code}" "$BaseUrl/google837a0fffd89d0450.html" }
$results += Invoke-Status -Name "POST /api/v1/auth/signup (public)" -Action { curl.exe --max-time 20 -sS -o NUL -w "%{http_code}" -H "Content-Type: application/json" -d "{}" "$BaseUrl/api/v1/auth/signup" } -Ok @(200,201,400,409,422)
$results += Invoke-Status -Name "POST /api/v1/auth/login (public)" -Action { curl.exe --max-time 20 -sS -o NUL -w "%{http_code}" -H "Content-Type: application/json" -d "{}" "$BaseUrl/api/v1/auth/login" } -Ok @(200,400,401,422)

if ($apiKey) {
    $results += Invoke-Status -Name "POST /api/v1/text/generate" -Action {
        $payload = @{ prompt = "prod check"; temperature = 0.2; max_output_tokens = 20 } | ConvertTo-Json
        try {
            $resp = Invoke-WebRequest -Uri "$BaseUrl/api/v1/text/generate" -Method POST -Headers @{ "x-api-key" = $apiKey } -ContentType "application/json" -Body $payload -TimeoutSec 20
            "{0}" -f [int]$resp.StatusCode
        }
        catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                "{0}" -f [int]$_.Exception.Response.StatusCode.value__
            }
            else {
                throw
            }
        }
    } -Ok @(200)
}
else {
    Write-Output "[WARN] API_KEYS missing in .env, skipped text generation check"
}

if ($adminKey) {
    $results += Invoke-Status -Name "GET /api/v1/admin/api-keys" -Action {
        try {
            $resp = Invoke-WebRequest -Uri "$BaseUrl/api/v1/admin/api-keys" -Method GET -Headers @{ "x-admin-key" = $adminKey } -TimeoutSec 20
            "{0}" -f [int]$resp.StatusCode
        }
        catch {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                "{0}" -f [int]$_.Exception.Response.StatusCode.value__
            }
            else {
                throw
            }
        }
    } -Ok @(200)
}
else {
    Write-Output "[WARN] ADMIN_API_KEY missing in .env, skipped admin endpoint check"
}

$healthJson = $null
try {
    $healthText = & curl.exe --max-time 20 -sS "$BaseUrl/health"
    $healthJson = $healthText | ConvertFrom-Json
    Write-Output ("[INFO] provider=" + $healthJson.provider + ", provider_ready=" + $healthJson.provider_ready + ", environment=" + $healthJson.environment)
}
catch {
    Write-Output "[FAIL] Could not parse /health JSON"
}

$failed = @($results | Where-Object { -not $_.passed }).Count
$total = $results.Count
Write-Output ""
Write-Output "Summary: $($total - $failed)/$total checks passed"

$report = [PSCustomObject]@{
    base_url = $BaseUrl
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    summary = [PSCustomObject]@{
        total = $total
        failed = $failed
        passed = $total - $failed
    }
    health = if ($healthJson) { [PSCustomObject]@{ provider = $healthJson.provider; provider_ready = $healthJson.provider_ready; environment = $healthJson.environment } } else { $null }
    results = $results
}

$report | ConvertTo-Json -Depth 8 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Output "Report written: $ReportPath"

if ($healthJson -and (-not $healthJson.provider_ready)) {
    Write-Output "[FAIL] Provider is not ready in live environment"
    exit 1
}

if ($healthJson -and ($healthJson.environment -ne "production")) {
    Write-Output "[FAIL] ENVIRONMENT is not production on live service"
    exit 1
}

if ($healthJson -and ($healthJson.PSObject.Properties.Name -contains "database_persistent") -and (-not [bool]$healthJson.database_persistent)) {
    Write-Output "[FAIL] Live service is using a non-persistent database (likely sqlite). Configure managed Postgres for production."
    exit 1
}

if ($failed -gt 0) {
    exit 1
}

exit 0
