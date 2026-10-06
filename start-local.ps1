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
        $request.Method = "GET"
        $request.Headers.Add("Origin", "http://localhost:8000")
        $response = $request.GetResponse()
        $response.Close()
        return $true
    } catch {
        return $false
    }
}

function Stop-StaleHelperIfPresent {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) {
        return
    }

    $helperPid = $listener.OwningProcess
    if (-not $helperPid) {
        return
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $helperPid" -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }

    $commandLine = $process.CommandLine
    if ($process.Name -eq "powershell.exe" -and $commandLine -match "windows_folder_picker\.ps1") {
        Write-Host "Encerrando seletor do Windows travado (PID $helperPid)..."
        Stop-Process -Id $helperPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 750
    }
}

function Start-HelperIfNeeded {
    $helperListenerReady = Test-LocalHttpListener "http://127.0.0.1:8765/status"
    if ($helperListenerReady) {
        return
    }

    Stop-StaleHelperIfPresent

    Write-Host "Iniciando seletor do Windows..."
    Start-Process powershell.exe -ArgumentList @(
        '-NoProfile',
        '-STA',
        '-ExecutionPolicy', 'Bypass',
        '-File', $helperScript
    ) -WorkingDirectory $repoRoot -WindowStyle Normal

    for ($attempt = 1; $attempt -le 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-LocalHttpListener "http://127.0.0.1:8765/status") {
            return
        }
    }

    throw "O seletor do Windows não respondeu em http://127.0.0.1:8765/status dentro do tempo esperado."
}

try {
    $dockerCommand = Get-Command docker -ErrorAction Stop
} catch {
    throw "O Docker CLI não foi encontrado no PATH. Instale o Docker Desktop e tente novamente."
}

Start-HelperIfNeeded

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
