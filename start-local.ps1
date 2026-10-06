param(
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$envFile = Join-Path $repoRoot ".env"
$pickerTokenFile = Join-Path $repoRoot ".folder-picker-token"
$composeFile = Join-Path $repoRoot "docker\docker-compose.yml"
$helperScript = Join-Path $repoRoot "tools\windows_folder_picker.ps1"

function Test-LocalHttpListener {
    param([string]$Uri)

    try {
        $request = [System.Net.HttpWebRequest]::Create($Uri)
        $request.Timeout = 1500
        $request.Method = "GET"
        $request.Headers.Add("Origin", "http://localhost:8000")
        if ($env:PICKER_TOKEN) {
            $request.Headers.Add("X-Picker-Token", $env:PICKER_TOKEN)
        }
        $response = $request.GetResponse()
        if ($Uri -like "*/status") {
            $reader = [System.IO.StreamReader]::new($response.GetResponseStream())
            try {
                $body = $reader.ReadToEnd() | ConvertFrom-Json
            } finally {
                $reader.Dispose()
                $response.Close()
            }
            return $body.token_required -eq $true
        }
        $response.Close()
        return $true
    } catch {
        return $false
    }
}

function Stop-StaleHelperIfPresent {
    $helperProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "powershell.exe" -and $_.CommandLine -match "windows_folder_picker\.ps1"
        }
    foreach ($process in $helperProcesses) {
        Write-Host "Encerrando seletor do Windows desatualizado (PID $($process.ProcessId))..."
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($helperProcesses) {
        Start-Sleep -Milliseconds 750
    }
}

function Initialize-PickerToken {
    $token = $null
    if (Test-Path -LiteralPath $pickerTokenFile) {
        $token = (Get-Content -LiteralPath $pickerTokenFile -Raw).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        $bytes = New-Object byte[] 32
        $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $generator.GetBytes($bytes)
        } finally {
            $generator.Dispose()
        }
        $token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
        [System.IO.File]::WriteAllText(
            $pickerTokenFile,
            $token,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
    $env:PICKER_TOKEN = $token
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

Initialize-PickerToken

try {
    $dockerCommand = Get-Command docker -ErrorAction Stop
} catch {
    throw "O Docker CLI não foi encontrado no PATH. Instale o Docker Desktop e tente novamente."
}

Start-HelperIfNeeded

Write-Host "Subindo a stack do app com Docker Compose..."
if (Test-Path -LiteralPath $envFile) {
    & $dockerCommand.Source compose --env-file $envFile -f $composeFile up -d --build
} else {
    & $dockerCommand.Source compose -f $composeFile up -d --build
}
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
