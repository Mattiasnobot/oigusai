$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$envPath = Join-Path $projectRoot '.env'

function Get-ProjectSetting {
    param([string]$Name, [string]$Default)
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { return $Default }
    $escaped = [regex]::Escape($Name)
    $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
        Where-Object { $_ -match "^\s*$escaped\s*=" } |
        Select-Object -Last 1
    if (-not $line) { return $Default }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value) { return $value }
    return $Default
}

function Get-OllamaModels {
    $tags = Invoke-RestMethod -Uri "$ollamaHost/api/tags" -TimeoutSec 3
    return @($tags.models | ForEach-Object {
        $modelName = [string]$_.name
        if (-not $modelName) { $modelName = [string]$_.model }
        $modelName
    })
}

function Test-ConfiguredModel {
    param([string]$Model, [string[]]$Available)
    if ($Available -contains $Model) { return $true }
    if ($Model -notmatch ':') {
        return [bool]($Available | Where-Object { ($_ -split ':', 2)[0] -eq $Model })
    }
    return $false
}

Write-Host ''
Write-Host 'ÕigusAI V9.1 pilootkäivitus' -ForegroundColor Cyan
Write-Host '1/5  Kontrollin projekti…'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Projekti .venv Python puudub. Loo virtuaalkeskkond ja paigalda requirements.txt.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'data\laws.json') -PathType Leaf)) {
    throw 'Kontrollitud õiguskorpus data\laws.json puudub.'
}

Write-Host '2/5  Kontrollin Ollamat…'
$ollamaHost = (Get-ProjectSetting 'OLLAMA_HOST' 'http://127.0.0.1:11434').TrimEnd('/')
try {
    $availableModels = Get-OllamaModels
} catch {
    $ollamaCommand = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
    if (-not $ollamaCommand) { throw 'Ollama ei tööta ja ollama.exe ei ole PATH-is.' }
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WindowStyle Hidden
    $availableModels = $null
    foreach ($attempt in 1..20) {
        Start-Sleep -Milliseconds 750
        try {
            $availableModels = Get-OllamaModels
            break
        } catch {}
    }
    if ($null -eq $availableModels) { throw 'Ollama ei käivitunud 15 sekundi jooksul.' }
}

$analysisModel = Get-ProjectSetting 'OLLAMA_MODEL' 'qwen3.5:9b-q4_K_M'
$visionModel = Get-ProjectSetting 'OLLAMA_VISION_MODEL' 'llama3.2-vision'
$embeddingModel = Get-ProjectSetting 'EMBEDDING_MODEL' 'bge-m3'
$missingModels = @()
foreach ($model in @($analysisModel, $embeddingModel, $visionModel)) {
    if (-not (Test-ConfiguredModel $model $availableModels)) { $missingModels += $model }
}
if ($missingModels.Count -gt 0) {
    $commands = ($missingModels | ForEach-Object { "ollama pull $_" }) -join [Environment]::NewLine
    throw "Puuduvad kohalikud mudelid:`n$commands"
}

$port = [int](Get-ProjectSetting 'APP_PORT' '8000')
$baseUrl = "http://127.0.0.1:$port"
$accessCode = Get-ProjectSetting 'APP_ACCESS_CODE' ''
$apiHeaders = @{}
if ($accessCode) { $apiHeaders['X-OigusAI-Access-Code'] = $accessCode }
Write-Host '3/5  Käivitan kohaliku veebiteenuse…'
$health = $null
try { $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 4 } catch {}
if ($null -eq $health) {
    Start-Process `
        -FilePath $pythonPath `
        -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '0.0.0.0', '--port', "$port") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 750
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 4
            break
        } catch {}
    }
}
if ($null -eq $health) { throw 'ÕigusAI veebiteenus ei käivitunud 30 sekundi jooksul.' }
if ([string]$health.version -ne '0.9.1') {
    throw "Pordil $port töötab teine ÕigusAI versioon ($($health.version)). Sulge see ja käivita uuesti."
}
if (-not $health.ready_for_demo) {
    throw "Valmisolekukontroll ebaõnnestus. Ava $baseUrl/health"
}

Write-Host '4/5  Soojendan esimest vastust (see võib võtta kuni paar minutit)…'
$warmupBody = @{
    case_description = 'Kui suur võib eluruumi üürilepingu tagatisraha olla ja kas seda saab maksta osadena?'
    current_message = 'Kui suur võib eluruumi üürilepingu tagatisraha olla ja kas seda saab maksta osadena?'
} | ConvertTo-Json -Depth 4
try {
    $null = Invoke-RestMethod `
        -Uri "$baseUrl/analyze" `
        -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Headers $apiHeaders `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($warmupBody)) `
        -TimeoutSec 360
    Write-Host '     Esimene vastus on soojendatud.' -ForegroundColor Green
} catch {
    Write-Warning "Soojenduspäring ei õnnestunud, kuid teenus töötab: $($_.Exception.Message)"
}

Write-Host '5/5  ÕigusAI on valmis.' -ForegroundColor Green
Write-Host "     $baseUrl"
if ($accessCode) {
    Write-Host '     Võrgukasutus on juurdepääsukoodiga kaitstud.' -ForegroundColor Green
} else {
    Write-Warning 'Juurdepääsukood puudub. Ära ava teenust avalikku internetti.'
}
Start-Process $baseUrl
