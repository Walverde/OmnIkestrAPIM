param(
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$composeFile = Join-Path $repoRoot "docker\docker-compose.yml"
$helperScript = Join-Path $repoRoot "tools\windows_folder_picker.ps1"

function Test-LocalHttpListener {
    param([string]$Uri)

    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Timeout = 1500
        $response = $request.GetResponse()
        $response.Close()
        return $true
    } catch {
        return $false
    }
}

try {
    $dockerCommand = Get-Command docker -ErrorAction Stop
} catch {
    throw "O Docker CLI não foi encontrado no PATH. Instale o Docker Desktop e tente novamente."
}

$helperListenerReady = Test-LocalHttpListener "http://127.0.0.1:8765/status"
if (-not $helperListenerReady) {
    Write-Host "Iniciando seletor do Windows..."
    Start-Process powershell.exe -ArgumentList @(
        '-NoProfile',
        '-STA',
        '-ExecutionPolicy', 'Bypass',
        '-File', $helperScript
    ) -WorkingDirectory $repoRoot -WindowStyle Normal
    Start-Sleep -Seconds 2
}

Write-Host "Subindo a stack do app com Docker Compose..."
& $dockerCommand.Source compose -f $composeFile up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "O comando do Docker Compose falhou. Verifique o ambiente e os arquivos de configuração."
}

if (-not $NoOpenBrowser) {
    Start-Process "http://localhost:8000/settings"
}

Write-Host ""
Write-Host "Aplicação iniciada."
Write-Host "Acesse: http://localhost:8000/settings"
Write-Host "O seletor do Windows foi inicializado e permanece aberto para seleção da pasta de anúncios."
