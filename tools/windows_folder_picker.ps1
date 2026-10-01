$ErrorActionPreference = "Stop"

if ([Threading.Thread]::CurrentThread.ApartmentState -ne "STA") {
    throw "Inicie este auxiliar com powershell.exe -STA."
}

Add-Type -AssemblyName System.Windows.Forms

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$composeFile = Join-Path $repoRoot "docker\docker-compose.yml"
$allowedOrigins = @("http://localhost:8000", "http://127.0.0.1:8000")
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:8765/")

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
    $response.Headers.Add("Access-Control-Allow-Headers", "Content-Type")
    $response.Headers.Add("Access-Control-Allow-Private-Network", "true")
    $response.Headers.Add("Vary", "Origin")

    $json = ConvertTo-Json -InputObject $body -Compress -Depth 5
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $response.ContentLength64 = $bytes.Length
    $response.OutputStream.Write($bytes, 0, $bytes.Length)
    $response.Close()
}

try {
    $listener.Start()
    Write-Host "Seletor de pastas ativo em http://127.0.0.1:8765"
    Write-Host "Mantenha esta janela aberta enquanto usa /settings."

    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $origin = $context.Request.Headers["Origin"]

        if ($origin -notin $allowedOrigins) {
            Write-JsonResponse $context 403 @{ detail = "Origem não autorizada." } "null"
            continue
        }

        if ($context.Request.HttpMethod -eq "OPTIONS") {
            $context.Response.StatusCode = 204
            $context.Response.Headers.Add("Access-Control-Allow-Origin", $origin)
            $context.Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            $context.Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type")
            $context.Response.Headers.Add("Access-Control-Allow-Private-Network", "true")
            $context.Response.Headers.Add("Vary", "Origin")
            $context.Response.Close()
            continue
        }

        try {
            $route = $context.Request.Url.AbsolutePath
            if ($context.Request.HttpMethod -eq "GET" -and $route -eq "/status") {
                Write-JsonResponse $context 200 @{
                    status = "ready"
                    products_dir = (Get-ConfiguredProductsPath)
                } $origin
                continue
            }

            if ($context.Request.HttpMethod -eq "POST" -and $route -eq "/pick") {
                $dialog = [System.Windows.Forms.FolderBrowserDialog]::new()
                $dialog.Description = "Selecione a pasta que contém seus anúncios"
                $dialog.ShowNewFolderButton = $true
                $initialPath = Get-ConfiguredProductsPath
                if (Test-Path -LiteralPath $initialPath -PathType Container) {
                    $dialog.SelectedPath = $initialPath
                }

                $dialogResult = $dialog.ShowDialog()
                if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) {
                    Write-JsonResponse $context 200 @{ cancelled = $true } $origin
                } else {
                    Write-JsonResponse $context 200 @{
                        cancelled = $false
                        path = [System.IO.Path]::GetFullPath($dialog.SelectedPath)
                    } $origin
                }
                $dialog.Dispose()
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
                    $output = & $dockerPath compose -f $composeFile up -d backend watcher 2>&1 | Out-String
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
