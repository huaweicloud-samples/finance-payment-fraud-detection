<#
.SYNOPSIS
    Refresh SWR credentials and update K8s dockerconfig secret

.DESCRIPTION
    This script gets a new SWR authorization token using华为云 API,
    then updates the Kubernetes default-secret with fresh docker login credentials.

.PARAMETER AK
    Huawei Cloud Access Key (optional, uses environment variable if not provided)

.PARAMETER SK
    Huawei Cloud Secret Key (optional, uses environment variable if not provided)

.EXAMPLE
    .\refresh_swr.ps1

#>

param(
    [string]$AK = $env:HW_ACCESS_KEY,
    [string]$SK = $env:HW_SECRET_KEY
)

if (-not $AK -or -not $SK) {
    Write-Host "ERROR: Please provide AK/SK via environment variables or parameters"
    Write-Host "Set HW_ACCESS_KEY and HW_SECRET_KEY, or run: .\refresh_swr.ps1 -AK '...' -SK '...'"
    exit 1
}

$REGION = 'cn-north-4'
$SWR_HOST = "swr.$REGION.myhuaweicloud.com"
$SWR_ORG = 'GT-dgcmy'
$SWR_INSTANCE_ID = 'spf'

# Step 1: Get IAM token
Write-Host "Step 1: Getting IAM token..." -ForegroundColor Cyan

# Build IAM authentication request with API V4 signing
$timestamp = [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$datestamp = $timestamp.Substring(0, 8)
$host = "iam.$REGION.myhuaweicloud.com"
$path = '/v3/auth/tokens'

$body = @{
    auth = @{
        identity = @{
            methods = @('password')
            password = @{
                user = @{
                    name = 'GT-dgcmy'
                    password = $SK
                    domain = @{ id = 'default' }
                }
            }
        }
        scope = @{
            project = @{ id = '0e6d048c10c0404a98c958566361c20d' }
        }
    }
} | ConvertTo-Json -Depth 3 -Compress

$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$bodyMd5 = [System.BitConverter]::ToString((Get-FileHash -InputBytes $bodyBytes -Algorithm MD5).Hash).Replace('-', '').ToLower()

# Canonical headers
$canonicalHeaders = "content-type:application/json`nhost:$host`nx-swa-timestamp:$timestamp`nx-swa-security-token:`n"
$signedHeaders = "content-type;host;x-swa-timestamp;x-swa-security-token"

# Canonical request
$canonicalRequest = "POST`n$path`n`n$canonicalHeaders`n$signedHeaders`n$bodyMd5"

# String to sign
$scope = "$datestamp/iam/iam_request"
$stringToSign = "HMAC-SHA256`n$timestamp`n$scope`n$(Get-Hash -InputData $canonicalRequest -Algorithm SHA256)"

# Signing key derivation
function Get-SigningKey($secretKey, $dateStamp, $region, $service) {
    $kDate = Get-Hmac -Key $secretKey -Data $dateStamp -Algorithm SHA256
    $kRegion = Get-Hmac -Key $kDate -Data $region -Algorithm SHA256
    $kService = Get-Hmac -Key $kRegion -Data $service -Algorithm SHA256
    Get-Hmac -Key $kService -Data 'swr_request' -Algorithm SHA256
}

$signingKey = Get-SigningKey -secretKey $SK -dateStamp $datestamp -region $REGION -service 'iam'
$signature = [System.BitConverter]::ToString((Get-Hmac -Key $signingKey -Data $stringToSign -Algorithm SHA256)).Replace('-', '').ToLower()

# Build authorization header
$authorization = "HMAC-SHA256 Credential=$AK/$scope, SignedHeaders=$signedHeaders, Signature=$signature"

$headers = @{
    'Authorization' = $authorization
    'x-swa-timestamp' = $timestamp
    'Content-Type' = 'application/json'
}

try {
    $response = Invoke-WebRequest -Uri "https://$host$path" -Method POST -Body $body -Headers $headers -TimeoutSec 15 -UseBasicParsing
    $tokenData = $response.Content | ConvertFrom-Json
    $iamToken = $tokenData.token.id
    $projectId = $tokenData.token.project.id
    Write-Host "  OK IAM token: $($iamToken.Substring(0, 30))..." -ForegroundColor Green
    Write-Host "  OK Project ID: $projectId" -ForegroundColor Green
} catch {
    Write-Host "IAM token failed: $_" -ForegroundColor Red
    Write-Host "Trying alternative method..." -ForegroundColor Yellow
    
    # Try to get project ID from user info
    $userPath = '/v3/users'
    $userHeaders = @{
        'Authorization' = $authorization
        'x-swa-timestamp' = $timestamp
        'X-Iam-User-Name' = 'GT-dgcmy'
    }
    
    try {
        $userResponse = Invoke-WebRequest -Uri "https://$host$userPath" -Method GET -Headers $userHeaders -TimeoutSec 10 -UseBasicParsing
        $userData = $userResponse.Content | ConvertFrom-Json
        $projectId = $userData.user.project_id
        Write-Host "  Got project ID: $projectId" -ForegroundColor Green
        
        # Retry with correct project ID
        $body.project.scope.project.id = $projectId
        $body = $body | ConvertTo-Json -Depth 3 -Compress
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $bodyMd5 = [System.BitConverter]::ToString((Get-FileHash -InputBytes $bodyBytes -Algorithm MD5).Hash).Replace('-', '').ToLower()
        
        $canonicalHeaders = "content-type:application/json`nhost:$host`nx-swa-timestamp:$timestamp`nx-swa-security-token:`n"
        $canonicalRequest = "POST`n$path`n`n$canonicalHeaders`n$signedHeaders`n$bodyMd5"
        $stringToSign = "HMAC-SHA256`n$timestamp`n$scope`n$(Get-Hash -InputData $canonicalRequest -Algorithm SHA256)"
        $signature = [System.BitConverter]::ToString((Get-Hmac -Key $signingKey -Data $stringToSign -Algorithm SHA256)).Replace('-', '').ToLower()
        $authorization = "HMAC-SHA256 Credential=$AK/$scope, SignedHeaders=$signedHeaders, Signature=$signature"
        
        $response = Invoke-WebRequest -Uri "https://$host$path" -Method POST -Body $body -Headers $headers -TimeoutSec 15 -UseBasicParsing
        $tokenData = $response.Content | ConvertFrom-Json
        $iamToken = $tokenData.token.id
        $projectId = $tokenData.token.project.id
        Write-Host "  OK (retry) IAM token: $($iamToken.Substring(0, 30))..." -ForegroundColor Green
    } catch {
        Write-Host "Failed to get project ID: $_" -ForegroundColor Red
        exit 1
    }
}

# Step 2: Get SWR long-term credential
Write-Host "`nStep 2: Getting SWR long-term credential..." -ForegroundColor Cyan

$swrPath = "/v2/credentials"
$swrBody = '{"validity_days":1}'
$swrHeaders = @{
    'Authorization' = $authorization
    'x-swa-timestamp' = $timestamp
    'Content-Type' = 'application/json'
    'x-swa-security-token' = $iamToken
}

try {
    $swrResponse = Invoke-WebRequest -Uri "https://$SWR_HOST$swrPath" -Method POST -Body $swrBody -Headers $swrHeaders -TimeoutSec 15 -UseBasicParsing
    $swrData = $swrResponse.Content | ConvertFrom-Json
    $token = $swrData.token
    Write-Host "  OK SWR token: $($token.Substring(0, 40))..." -ForegroundColor Green
} catch {
    Write-Host "SWR credential failed: $_" -ForegroundColor Red
    Write-Host "Trying instance path..." -ForegroundColor Yellow
    
    # Try with instance_id in path
    $swrPath2 = "/v2/instances/$SWR_INSTANCE_ID/credentials"
    $swrBody2 = '{"validity_days":1}'
    $swrHeaders['Authorization'] = $authorization
    
    try {
        $swrResponse = Invoke-WebRequest -Uri "https://$SWR_HOST$swrPath2" -Method POST -Body $swrBody2 -Headers $swrHeaders -TimeoutSec 15 -UseBasicParsing
        $swrData = $swrResponse.Content | ConvertFrom-Json
        $token = $swrData.token
        Write-Host "  OK (instance path) SWR token: $($token.Substring(0, 40))..." -ForegroundColor Green
    } catch {
        Write-Host "Also failed: $_" -ForegroundColor Red
        exit 1
    }
}

# Step 3: Get docker login credentials
Write-Host "`nStep 3: Getting docker login..." -ForegroundColor Cyan

$loginUrl = "https://$SWR_HOST/v2/token?account=$SWR_ORG&token=$token"
try {
    $loginResponse = Invoke-WebRequest -Uri $loginUrl -Method GET -Headers @{ 'Accept' = 'application/json' } -TimeoutSec 10 -UseBasicParsing
    $loginData = $loginResponse.Content | ConvertFrom-Json
    $dockerUser = $loginData.username
    $dockerPassword = $loginData.password
    Write-Host "  OK Docker user: $dockerUser" -ForegroundColor Green
} catch {
    Write-Host "Docker login failed: $_" -ForegroundColor Red
    exit 1
}

# Step 4: Update K8s secret
Write-Host "`nStep 4: Updating K8s default-secret..." -ForegroundColor Cyan

$dockerConfig = @{
    auths = @{
        $SWR_HOST = @{
            username = $dockerUser
            password = $dockerPassword
            auth = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes("$dockerUser:$dockerPassword"))
        }
        '100.125.0.78:20202' = @{
            auth = [Convert]::ToBase64String([System.Text.Encoding]::ASCII.GetBytes("$dockerUser:$dockerPassword"))
        }
    }
} | ConvertTo-Json -Depth 3

$dockerConfigB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($dockerConfig))

$kubeconfig = 'D:\workbuddy\huaweicloud\finance-payment-fraud-detection\infra\kubeconfig.yaml'
$tmpFile = 'D:\workbuddy\huaweicloud\finance-payment-fraud-detection\tmp_secret.yaml'

$yamlContent = @"
apiVersion: v1
kind: Secret
metadata:
  name: default-secret
  namespace: spf-app
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: $dockerConfigB64
"@

Set-Content -Path $tmpFile -Value $yamlContent -Encoding UTF8

$kubectlCmd = "kubectl --kubeconfig `"$kubeconfig`" -n spf-app apply -f `"$tmpFile`""
Write-Host "  Executing: $kubectlCmd" -ForegroundColor Yellow

& kubectl --kubeconfig $kubeconfig -n spf-app apply -f $tmpFile

Remove-Item $tmpFile -Force

Write-Host "`n=== DONE ===" -ForegroundColor Green
Write-Host "SWR credentials refreshed and K8s secret updated!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (run in PowerShell):" -ForegroundColor Yellow
Write-Host "  \`$k = `\"D:\workbuddy\huaweicloud\finance-payment-fraud-detection\infra\kubeconfig.yaml`""
Write-Host "  kubectl --kubeconfig \`$k -n spf-app rollout restart statefulset/postgres"
Write-Host "  kubectl --kubeconfig \`$k -n spf-app rollout restart deployment/anomaly-detector"
Write-Host "  kubectl --kubeconfig \`$k -n spf-app rollout restart cronjob/data-collector"
Write-Host ""
Write-Host "Monitor progress:" -ForegroundColor Yellow
Write-Host "  while (-not (kubectl --kubeconfig \`$k -n spf-app get pods 2>&1 | Select-String `\"Running`\" | Measure-Object).Count -ge 3) { Start-Sleep 10; kubectl --kubeconfig \`$k -n spf-app get pods }"
