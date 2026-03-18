param(
    [string]$BaseUrl = "http://localhost:8000",
    [switch]$CheckOnly,
    [switch]$StartStack,
    [switch]$RunEmailJobs,
    [switch]$SkipSmoke,
    [switch]$SkipLifecycle
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$envPath = Join-Path $projectRoot ".env"
$results = @()

function Add-Result {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    $script:results += [PSCustomObject]@{
        Name = $Name
        Passed = $Passed
        Detail = $Detail
    }

    if ($Passed) {
        Write-Output "[PASS] $Name - $Detail"
    }
    else {
        Write-Output "[FAIL] $Name - $Detail"
    }
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [string]$EnvPath
    )

    if (-not (Test-Path $EnvPath)) { return "" }

    $line = Get-Content $EnvPath | Where-Object { $_ -match "^\s*$Key\s*=\s*" } | Select-Object -First 1
    if (-not $line) { return "" }

    $value = $line -replace "^\s*$Key\s*=\s*", ""
    return $value.Trim().Trim('"').Trim("'")
}

function Probe-Http {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [ValidateSet("GET", "POST")]
        [string]$Method = "GET",
        [hashtable]$Headers
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -Method $Method -Headers $Headers -TimeoutSec 15
        return [PSCustomObject]@{
            Ok = $true
            StatusCode = [int]$response.StatusCode
            Error = ""
        }
    }
    catch {
        $status = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $status = [int]$_.Exception.Response.StatusCode.value__
        }
        return [PSCustomObject]@{
            Ok = $false
            StatusCode = $status
            Error = $_.Exception.Message
        }
    }
}

Set-Location $projectRoot

if (-not (Test-Path $envPath)) {
    Add-Result -Name ".env exists" -Passed $false -Detail "Missing .env file"
}
else {
    Add-Result -Name ".env exists" -Passed $true -Detail "Found .env"
}

$adminApiKey = Get-EnvValue -Key "ADMIN_API_KEY" -EnvPath $envPath
$publicBaseUrl = Get-EnvValue -Key "PUBLIC_BASE_URL" -EnvPath $envPath
$environmentMode = (Get-EnvValue -Key "ENVIRONMENT" -EnvPath $envPath).ToLowerInvariant()
$providerName = (Get-EnvValue -Key "PROVIDER" -EnvPath $envPath).ToLowerInvariant()
$providerFallbackOrder = (Get-EnvValue -Key "PROVIDER_FALLBACK_ORDER" -EnvPath $envPath).ToLowerInvariant()
$openaiApiKey = Get-EnvValue -Key "OPENAI_API_KEY" -EnvPath $envPath
$togetherApiKey = Get-EnvValue -Key "TOGETHER_API_KEY" -EnvPath $envPath
$groqApiKey = Get-EnvValue -Key "GROQ_API_KEY" -EnvPath $envPath
$geminiApiKey = Get-EnvValue -Key "GEMINI_API_KEY" -EnvPath $envPath
$authTokenSecret = Get-EnvValue -Key "AUTH_TOKEN_SECRET" -EnvPath $envPath
$publicPathsRaw = Get-EnvValue -Key "PUBLIC_PATHS" -EnvPath $envPath
$databaseUrl = (Get-EnvValue -Key "DATABASE_URL" -EnvPath $envPath)
$razorpayKeyId = Get-EnvValue -Key "RAZORPAY_KEY_ID" -EnvPath $envPath
$razorpayKeySecret = Get-EnvValue -Key "RAZORPAY_KEY_SECRET" -EnvPath $envPath
$razorpayWebhookSecret = Get-EnvValue -Key "RAZORPAY_WEBHOOK_SECRET" -EnvPath $envPath
$smtpHost = Get-EnvValue -Key "SMTP_HOST" -EnvPath $envPath
$emailFromAddress = Get-EnvValue -Key "EMAIL_FROM_ADDRESS" -EnvPath $envPath

Add-Result -Name "ADMIN_API_KEY configured" -Passed ([bool]$adminApiKey) -Detail $(if ($adminApiKey) { "Set" } else { "Missing" })
Add-Result -Name "PUBLIC_BASE_URL configured" -Passed ([bool]$publicBaseUrl) -Detail $(if ($publicBaseUrl) { $publicBaseUrl } else { "Missing" })
Add-Result -Name "ENVIRONMENT is production" -Passed ($environmentMode -eq "production") -Detail $(if ($environmentMode) { $environmentMode } else { "Missing" })
Add-Result -Name "AUTH_TOKEN_SECRET strength" -Passed ($authTokenSecret.Length -ge 32) -Detail $(if ($authTokenSecret.Length -ge 32) { "Length $($authTokenSecret.Length)" } else { "Too short (min 32 chars)" })
Add-Result -Name "Production database persistence" -Passed (-not ($databaseUrl.ToLowerInvariant().StartsWith("sqlite"))) -Detail $(if ($databaseUrl) { $databaseUrl } else { "Missing DATABASE_URL" })
Add-Result -Name "Razorpay credentials" -Passed ([bool]$razorpayKeyId -and [bool]$razorpayKeySecret -and [bool]$razorpayWebhookSecret) -Detail $(if ($razorpayKeyId -and $razorpayKeySecret -and $razorpayWebhookSecret) { "Configured" } else { "Missing one or more values" })
Add-Result -Name "SMTP essentials" -Passed ([bool]$smtpHost -and [bool]$emailFromAddress) -Detail $(if ($smtpHost -and $emailFromAddress) { "Configured" } else { "Set SMTP_HOST and EMAIL_FROM_ADDRESS" })

$providerConfigured = $false
switch ($providerName) {
    "openai" { $providerConfigured = [bool]$openaiApiKey }
    "together" { $providerConfigured = [bool]$togetherApiKey }
    "groq" { $providerConfigured = [bool]$groqApiKey }
    "gemini" { $providerConfigured = [bool]$geminiApiKey }
    "ollama" { $providerConfigured = $true }
    "mock" { $providerConfigured = $true }
    "auto" {
        $providerConfigured = [bool]$openaiApiKey -or [bool]$togetherApiKey -or [bool]$groqApiKey -or [bool]$geminiApiKey
    }
    default { $providerConfigured = $false }
}

Add-Result -Name "Provider credentials configured" -Passed $providerConfigured -Detail $(if ($providerConfigured) { "Provider=$providerName" } else { "Provider=$providerName missing required credentials" })

$requiredPublicPaths = @(
    "/api/v1/auth/signup",
    "/api/v1/auth/login",
    "/api/v1/auth/request-reset",
    "/api/v1/auth/reset-password"
)
$publicPaths = @()
if ($publicPathsRaw) {
    $publicPaths = $publicPathsRaw.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
$missingPublicPaths = @($requiredPublicPaths | Where-Object { $_ -notin $publicPaths })
Add-Result -Name "Auth routes public allowlist" -Passed ($missingPublicPaths.Count -eq 0) -Detail $(if ($missingPublicPaths.Count -eq 0) { "Configured" } else { "Missing: $($missingPublicPaths -join ', ')" })

if ($StartStack) {
    Write-Output "Starting stack..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
    if ($LASTEXITCODE -eq 0) {
        Add-Result -Name "Stack startup" -Passed $true -Detail "start.ps1 succeeded"
    }
    else {
        Add-Result -Name "Stack startup" -Passed $false -Detail "start.ps1 failed"
    }
}

$health = Probe-Http -Url "$BaseUrl/health"
if ($health.Ok -and $health.StatusCode -eq 200) {
    Add-Result -Name "Health endpoint" -Passed $true -Detail "HTTP 200"

    try {
        $healthBody = Invoke-WebRequest -Uri "$BaseUrl/health" -Method GET -TimeoutSec 15
        $healthJson = $healthBody.Content | ConvertFrom-Json
        $healthProviderReady = [bool]$healthJson.provider_ready
        $healthEnvironment = [string]$healthJson.environment

        Add-Result -Name "Health provider ready" -Passed $healthProviderReady -Detail $(if ($healthProviderReady) { "true" } else { "false" })
        Add-Result -Name "Health environment production" -Passed ($healthEnvironment -eq "production") -Detail $(if ($healthEnvironment) { $healthEnvironment } else { "missing" })
    }
    catch {
        Add-Result -Name "Health JSON parse" -Passed $false -Detail "Could not parse /health response"
    }
}
else {
    $detail = if ($health.StatusCode) { "HTTP $($health.StatusCode)" } else { $health.Error }
    Add-Result -Name "Health endpoint" -Passed $false -Detail $detail
}

$publicPlans = Probe-Http -Url "$BaseUrl/api/v1/public/plans"
if ($publicPlans.Ok -and $publicPlans.StatusCode -eq 200) {
    Add-Result -Name "Public plans endpoint" -Passed $true -Detail "HTTP 200"
}
else {
    $detail = if ($publicPlans.StatusCode) { "HTTP $($publicPlans.StatusCode)" } else { $publicPlans.Error }
    Add-Result -Name "Public plans endpoint" -Passed $false -Detail $detail
}

$robots = Probe-Http -Url "$BaseUrl/robots.txt"
Add-Result -Name "robots.txt reachable" -Passed ($robots.Ok -and $robots.StatusCode -eq 200) -Detail $(if ($robots.StatusCode) { "HTTP $($robots.StatusCode)" } else { $robots.Error })

$sitemap = Probe-Http -Url "$BaseUrl/sitemap.xml"
Add-Result -Name "sitemap.xml reachable" -Passed ($sitemap.Ok -and $sitemap.StatusCode -eq 200) -Detail $(if ($sitemap.StatusCode) { "HTTP $($sitemap.StatusCode)" } else { $sitemap.Error })

$googleVerify = Probe-Http -Url "$BaseUrl/google837a0fffd89d0450.html"
Add-Result -Name "Google verification file" -Passed ($googleVerify.Ok -and $googleVerify.StatusCode -eq 200) -Detail $(if ($googleVerify.StatusCode) { "HTTP $($googleVerify.StatusCode)" } else { $googleVerify.Error })

$signupPublic = Probe-Http -Url "$BaseUrl/api/v1/auth/signup" -Method "POST"
$signupPublicBlocked = ($signupPublic.StatusCode -eq 401 -or $signupPublic.StatusCode -eq 403)
Add-Result -Name "Signup endpoint public access" -Passed (-not $signupPublicBlocked) -Detail $(if ($signupPublic.StatusCode) { "HTTP $($signupPublic.StatusCode)" } else { $signupPublic.Error })

$loginPublic = Probe-Http -Url "$BaseUrl/api/v1/auth/login" -Method "POST"
$loginPublicBlocked = ($loginPublic.StatusCode -eq 401 -or $loginPublic.StatusCode -eq 403)
Add-Result -Name "Login endpoint public access" -Passed (-not $loginPublicBlocked) -Detail $(if ($loginPublic.StatusCode) { "HTTP $($loginPublic.StatusCode)" } else { $loginPublic.Error })

if (-not $SkipSmoke) {
    Write-Output "Running smoke tests..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File .\smoke-test.ps1 -BaseUrl $BaseUrl -ReportPath .\smoke-test-report.json
    Add-Result -Name "Smoke tests" -Passed ($LASTEXITCODE -eq 0) -Detail $(if ($LASTEXITCODE -eq 0) { "Passed" } else { "Failed" })
}
else {
    Add-Result -Name "Smoke tests" -Passed $true -Detail "Skipped"
}

if (-not $SkipLifecycle) {
    if ($CheckOnly) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-lifecycle.ps1 -BaseUrl $BaseUrl -SkipRun
    }
    else {
        & powershell -NoProfile -ExecutionPolicy Bypass -File .\verify-lifecycle.ps1 -BaseUrl $BaseUrl
    }
    Add-Result -Name "Lifecycle verifier" -Passed ($LASTEXITCODE -eq 0) -Detail $(if ($LASTEXITCODE -eq 0) { "Passed" } else { "Failed" })

    if ($RunEmailJobs -and -not $CheckOnly) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File .\run-email-jobs.ps1 -BaseUrl $BaseUrl
        Add-Result -Name "Run email jobs" -Passed ($LASTEXITCODE -eq 0) -Detail $(if ($LASTEXITCODE -eq 0) { "Completed" } else { "Failed" })
    }
}
else {
    Add-Result -Name "Lifecycle verifier" -Passed $true -Detail "Skipped"
}

$total = $results.Count
$passed = ($results | Where-Object { $_.Passed }).Count
$failed = $total - $passed

Write-Output ""
Write-Output "Go-live summary: $passed/$total passed, $failed failed"

if ($failed -gt 0) { exit 1 }
exit 0
