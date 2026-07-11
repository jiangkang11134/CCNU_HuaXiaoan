# Yuxi Initialization Script for PowerShell
# This script helps set up the environment for the Yuxi project
# Note: API keys will be visible during input - use with care

function New-RandomHex($ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        return -join ($bytes | ForEach-Object { $_.ToString("x2") })
    } finally {
        $rng.Dispose()
    }
}

function Test-EnvValue($Name) {
    return [bool](Select-String -Path ".env" -Pattern "^$Name=.+" -Quiet)
}

function Ensure-JwtEnv {
    if ((Test-EnvValue "JWT_SECRET_KEY") -and (Test-EnvValue "YUXI_INSTANCE_ID")) {
        return
    }

    Write-Host "JWT security settings are missing in .env." -ForegroundColor Yellow
    $JWT_SECRET_KEY = Read-Host "Please enter your JWT_SECRET_KEY (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($JWT_SECRET_KEY)) {
        $JWT_SECRET_KEY = New-RandomHex 32
        Write-Host "Generated JWT_SECRET_KEY and saved it to .env." -ForegroundColor Green
    }

    $YUXI_INSTANCE_ID = Read-Host "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($YUXI_INSTANCE_ID)) {
        $YUXI_INSTANCE_ID = "instance-$(New-RandomHex 8)"
        Write-Host "Generated YUXI_INSTANCE_ID and saved it to .env." -ForegroundColor Green
    }

    @"

# JWT security settings
JWT_SECRET_KEY=$JWT_SECRET_KEY
YUXI_INSTANCE_ID=$YUXI_INSTANCE_ID
"@ | Add-Content -Path ".env" -Encoding UTF8
}

Write-Host "🚀 Initializing Yuxi project..." -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Check if .env file exists
if (Test-Path ".env") {
    Write-Host "✅ .env file already exists. Skipping environment setup." -ForegroundColor Green
    Ensure-JwtEnv
} else {
    Write-Host "📝 .env file not found. Let's set up your environment variables." -ForegroundColor Yellow
    Write-Host ""

    Write-Host ""
    Write-Host "JWT security settings" -ForegroundColor Yellow
    $JWT_SECRET_KEY = Read-Host "Please enter your JWT_SECRET_KEY (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($JWT_SECRET_KEY)) {
        $JWT_SECRET_KEY = New-RandomHex 32
        Write-Host "Generated JWT_SECRET_KEY and saved it to .env." -ForegroundColor Green
    }

    $YUXI_INSTANCE_ID = Read-Host "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($YUXI_INSTANCE_ID)) {
        $YUXI_INSTANCE_ID = "instance-$(New-RandomHex 8)"
        Write-Host "Generated YUXI_INSTANCE_ID and saved it to .env." -ForegroundColor Green
    }

    # Create .env file
    $envContent = @"
# JWT security settings
JWT_SECRET_KEY=$JWT_SECRET_KEY
YUXI_INSTANCE_ID=$YUXI_INSTANCE_ID
"@

    $envContent | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "✅ .env file created successfully!" -ForegroundColor Green
    Write-Host "Model, OCR, URL whitelist and Tavily keys can be configured in the admin UI." -ForegroundColor Green

    # Clear the variables from memory
    Remove-Variable -Name "JWT_SECRET_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "YUXI_INSTANCE_ID" -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "📦 Pulling Docker images..." -ForegroundColor Cyan
Write-Host "=========================" -ForegroundColor Cyan

# List of Docker images to pull
$images = @(
    "python:3.13-slim",
    "node:24-slim",
    "node:24-alpine",
    "milvusdb/milvus:v2.5.6",
    "neo4j:5.26",
    "minio/minio:RELEASE.2023-03-20T20-16-18Z",
    "ghcr.io/astral-sh/uv:0.11.26",
    "nginx:alpine",
    "quay.io/coreos/etcd:v3.5.5",
    "postgres:16",
    "redis:7-alpine"
)

# Pull each image
foreach ($image in $images) {
    Write-Host "🔄 Pulling ${image}..." -ForegroundColor Yellow
    try {
        & scripts/pull_image.ps1 $image
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Successfully pulled ${image}" -ForegroundColor Green
        } else {
            Write-Host "❌ Failed to pull ${image}" -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "❌ Error pulling ${image}: $_" -ForegroundColor Red
        exit 1
    }
}

$sandboxImage = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
Write-Host "🔄 Pulling ${sandboxImage}..." -ForegroundColor Yellow
docker pull $sandboxImage
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Successfully pulled ${sandboxImage}" -ForegroundColor Green
} else {
    Write-Host "❌ Failed to pull ${sandboxImage}" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "🎉 Initialization complete!" -ForegroundColor Green
Write-Host "==========================" -ForegroundColor Green
Write-Host "You can now run: docker compose up -d --build" -ForegroundColor Cyan
Write-Host "This will start all services in development mode with hot-reload enabled." -ForegroundColor Cyan
