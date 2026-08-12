# Deploy pdf_splitter source to Linux server (no .venv, models, output)
# Usage:
#   .\scripts\deploy_to_server.ps1
#   .\scripts\deploy_to_server.ps1 -Server sonth@10.10.6.134 -RemoteDir "/home/sonth/pdf_splitter"

param(
    [string]$Server = "sonth@10.10.6.134",
    [string]$RemoteDir = "/home/sonth/pdf_splitter",
    [string]$ZipName = "pdf_splitter_src.zip"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$ZipPath = Join-Path $env:TEMP $ZipName

Write-Host "Project: $ProjectRoot"
Write-Host "Zip: $ZipPath"

if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

$Stage = Join-Path $env:TEMP "pdf_splitter_deploy_stage"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null

$excludeDirs = @(
    '.venv', 'venv', 'models', 'output', 'output_test', 'output_test_3state',
    'work_minio', '__pycache__', '.git', 'logs'
)
robocopy $ProjectRoot $Stage /E /XD $excludeDirs /XF *.pdf *.pth *.pt *.zip .env /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "logs") | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $Stage "logs\.gitkeep") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "output") | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $Stage "output\.gitkeep") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "models") | Out-Null
New-Item -ItemType File -Force -Path (Join-Path $Stage "models\.gitkeep") | Out-Null

Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath $ZipPath -Force
Remove-Item $Stage -Recurse -Force

$ZipMb = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
Write-Host "Created zip $ZipMb MB"

$remoteZip = "/tmp/$ZipName"
Write-Host "SCP to ${Server}:$remoteZip"
& scp $ZipPath "${Server}:${remoteZip}"
if ($LASTEXITCODE -ne 0) {
    throw "scp failed. Test: ssh $Server"
}

Write-Host "Unpack on server..."
$bash = @"
set -e
mkdir -p '$RemoteDir'
cd '$RemoteDir'
unzip -o '$remoteZip'
rm -f '$remoteZip'
echo Deployed to $(pwd)
ls -la
"@
& ssh $Server $bash
if ($LASTEXITCODE -ne 0) {
    throw "ssh unpack failed"
}

Write-Host "Done. On server run:"
Write-Host "  cd $RemoteDir"
Write-Host "  cp .env.example .env"
Write-Host "  python3 -m venv .venv && source .venv/bin/activate"
Write-Host "  pip install -r requirements.txt"
Write-Host "  python minio_run.py --setup"
