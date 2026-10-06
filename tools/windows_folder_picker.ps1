$ErrorActionPreference = "Stop"

if ([Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    throw "Inicie este auxiliar com powershell.exe -STA."
}

Add-Type -AssemblyName System.Windows.Forms

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$composeFile = Join-Path $repoRoot "docker\docker-compose.yml"
$logFile = Join-Path $repoRoot "tools\windows_folder_picker.log"
$pickerToken = $env:PICKER_TOKEN
if ([string]::IsNullOrWhiteSpace($pickerToken)) {
    throw "PICKER_TOKEN não definido. Inicie o seletor por start-local.cmd."
}
$allowedOrigins = @("http://localhost:8000", "http://127.0.0.1:8000")
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:8765/")

function Write-Log {
    param([string]$Message)
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$timestamp] $Message"
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Get-ConfiguredProductsPath {
    if (Test-Path -LiteralPath $envFile) {
        $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^\s*PRODUCTS_DIR\s*=' } | Select-Object -Last 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim()
            if ($value.Length -ge 2 -and $value[0] -eq "'" -and $value[$value.Length - 1] -eq "'") {
                $escapedQuote = ([string][char]92) + "'"
                $value = $value.Substring(1, $value.Length - 2).Replace($escapedQuote, "'")
            }
            return $value.Replace("/", "\")
        }
    }

    return Join-Path $repoRoot "Products"
}

function Write-JsonResponse($context, [int]$statusCode, $body, [string]$origin) {
    $response = $context.Response
    $response.StatusCode = $statusCode
    $response.ContentType = "application/json; charset=utf-8"
    $response.Headers.Add("Access-Control-Allow-Origin", $origin)
    $response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    $response.Headers.Add("Access-Control-Allow-Headers", "Content-Type, X-Picker-Token")
    $response.Headers.Add("Access-Control-Allow-Private-Network", "true")
    $response.Headers.Add("Vary", "Origin")

    $json = ConvertTo-Json -InputObject $body -Compress -Depth 5
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $response.ContentLength64 = $bytes.Length
    $response.OutputStream.Write($bytes, 0, $bytes.Length)
    $response.Close()
}

try {
    Write-Log "Helper iniciado. Escutando em http://127.0.0.1:8765/"
    $listener.Start()
    Write-Host "Seletor de pastas ativo em http://127.0.0.1:8765"
    Write-Host "Mantenha esta janela aberta enquanto usa /settings."

    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $origin = $context.Request.Headers["Origin"]
        Write-Log "Http request recebida: $($context.Request.HttpMethod) $($context.Request.Url.AbsolutePath) Origin=$origin"

        if ($origin -notin $allowedOrigins) {
            Write-JsonResponse $context 403 @{ detail = "Origem não autorizada." } "null"
            continue
        }

        if ($context.Request.HttpMethod -eq "OPTIONS") {
            $context.Response.StatusCode = 204
            $context.Response.Headers.Add("Access-Control-Allow-Origin", $origin)
            $context.Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            $context.Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type, X-Picker-Token")
            $context.Response.Headers.Add("Access-Control-Allow-Private-Network", "true")
            $context.Response.Headers.Add("Vary", "Origin")
            $context.Response.Close()
            continue
        }

        if ($context.Request.Headers["X-Picker-Token"] -cne $pickerToken) {
            Write-JsonResponse $context 401 @{ detail = "Token do seletor inválido." } $origin
            continue
        }

        try {
            $route = $context.Request.Url.AbsolutePath
            if ($context.Request.HttpMethod -eq "GET" -and $route -eq "/status") {
                Write-JsonResponse $context 200 @{
                    status = "ready"
                    products_dir = (Get-ConfiguredProductsPath)
                    token_required = $true
                } $origin
                continue
            }

            if ($context.Request.HttpMethod -eq "POST" -and $route -eq "/pick") {
                $dialog = $null
                $owner = $null
                try {
                    Write-Log "Abrindo FolderBrowserDialog..."
                    $owner = [System.Windows.Forms.Form]::new()
                    $owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
                    $owner.Opacity = 0.01
                    $owner.TopMost = $true
                    $owner.ShowInTaskbar = $false
                    $owner.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
                    $owner.Location = [System.Drawing.Point]::new(-2000, -2000)
                    $owner.Size = [System.Drawing.Size]::new(1, 1)
                    $owner.Show()
                    $owner.Activate()

                    $dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
                    $dialog.Description = "Selecione a pasta que contém seus anúncios"
                    $dialog.ShowNewFolderButton = $true
                    $dialog.RootFolder = [System.Environment+SpecialFolder]::MyComputer
                    $initialPath = Get-ConfiguredProductsPath
                    if (Test-Path -LiteralPath $initialPath -PathType Container) {
                        $dialog.SelectedPath = $initialPath
                    }

                    $dialogResult = $dialog.ShowDialog($owner)
                    if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) {
                        Write-Log "FolderBrowserDialog cancelado pelo usuário."
                        Write-JsonResponse $context 200 @{ cancelled = $true } $origin
                    } else {
                        $selectedPath = [System.IO.Path]::GetFullPath($dialog.SelectedPath)
                        Write-Log "Pasta selecionada: $selectedPath"
                        Write-JsonResponse $context 200 @{
                            cancelled = $false
                            path = $selectedPath
                        } $origin
                    }
                } catch {
                    Write-Log "Erro ao abrir FolderBrowserDialog: $($_.Exception.Message)"
                    Write-JsonResponse $context 500 @{ detail = $_.Exception.Message } $origin
                } finally {
                    if ($null -ne $owner -and -not $owner.IsDisposed) {
                        $owner.Close()
                        $owner.Dispose()
                    }
                    if ($null -ne $dialog -and -not $dialog.IsDisposed) {
                        $dialog.Dispose()
                    }
                }
                continue
            }

            if ($context.Request.HttpMethod -eq "POST" -and $route -eq "/apply") {
                $reader = [System.IO.StreamReader]::new($context.Request.InputStream, $context.Request.ContentEncoding)
                try {
                    $payload = $reader.ReadToEnd() | ConvertFrom-Json
                } finally {
                    $reader.Dispose()
                }

                $selectedPath = [System.IO.Path]::GetFullPath([string]$payload.path)
                if (-not (Test-Path -LiteralPath $selectedPath -PathType Container)) {
                    Write-JsonResponse $context 400 @{ detail = "A pasta selecionada não existe mais." } $origin
                    continue
                }

                $composePath = $selectedPath.Replace("\", "/").Replace("'", "\'")
                $lines = @()
                if (Test-Path -LiteralPath $envFile) {
                    $lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -notmatch '^\s*PRODUCTS_DIR\s*=' })
                }
                $lines += "PRODUCTS_DIR='$composePath'"
                [System.IO.File]::WriteAllLines($envFile, $lines, [System.Text.UTF8Encoding]::new($false))

                $docker = Get-Command docker -ErrorAction SilentlyContinue
                $dockerPath = if ($docker) { $docker.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }
                if (-not (Test-Path -LiteralPath $dockerPath)) {
                    Write-JsonResponse $context 500 @{ detail = "Docker CLI não encontrado. Abra um novo terminal e tente novamente." } $origin
                    continue
                }

                $previousProductsDir = $env:PRODUCTS_DIR
                $env:PRODUCTS_DIR = $composePath
                Push-Location $repoRoot
                try {
                    $output = & $dockerPath compose --env-file $envFile -f $composeFile up -d backend watcher 2>&1 | Out-String
                    $exitCode = $LASTEXITCODE
                } finally {
                    Pop-Location
                    if ($null -eq $previousProductsDir) {
                        Remove-Item Env:PRODUCTS_DIR -ErrorAction SilentlyContinue
                    } else {
                        $env:PRODUCTS_DIR = $previousProductsDir
                    }
                }

                if ($exitCode -ne 0) {
                    Write-JsonResponse $context 500 @{
                        detail = "A pasta foi salva, mas o Compose não conseguiu recriar os serviços."
                        output = $output.Trim()
                    } $origin
                    continue
                }

                Write-JsonResponse $context 200 @{
                    status = "applied"
                    products_dir = $selectedPath
                    message = "Backend e watcher recriados com a pasta selecionada."
                } $origin
                continue
            }

            Write-JsonResponse $context 404 @{ detail = "Rota não encontrada." } $origin
        } catch {
            Write-JsonResponse $context 500 @{ detail = $_.Exception.Message } $origin
        }
    }
} finally {
    if ($listener.IsListening) {
        $listener.Stop()
    }
    $listener.Close()
}
