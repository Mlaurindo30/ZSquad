# PowerShell Setup Script - Agents Squad (Instalação e Provisionamento Total)
$ErrorActionPreference = "Stop"

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "   AGENTS SQUAD — INSTALADOR TOTAL ZERO-TO-HERO        " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$scriptRoot = $PSScriptRoot
Set-Location $scriptRoot

# 1. Verifica Python 3.11+
$pythonCmd = "python"
$hasPython = $false
try {
    $pyVersionOutput = & $pythonCmd --version 2>&1
    if ($pyVersionOutput -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -eq 3 -and $minor -ge 11) {
            $hasPython = $true
            Write-Host "  [OK] Python $major.$minor detectado no PATH." -ForegroundColor Green
        }
    }
} catch {}

if (-not $hasPython) {
    try {
        $pyVersionOutput = py -3.11 --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = "py -3.11"
            $hasPython = $true
            Write-Host "  [OK] Python 3.11+ detectado via py launcher." -ForegroundColor Green
        }
    } catch {}
}

if (-not $hasPython) {
    Write-Host "Aviso: Python 3.11+ não encontrado. Tentando instalar via winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        try {
            & python --version
            $pythonCmd = "python"
            $hasPython = $true
            Write-Host "  [OK] Python instalado com sucesso via winget." -ForegroundColor Green
        } catch {
            Write-Host "Erro: Python 3.11+ não pôde ser instalado automaticamente. Instale Python 3.11+ manualmente." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "Erro: Python 3.11+ não encontrado e winget não está disponível. Instale Python 3.11+ manualmente." -ForegroundColor Red
        exit 1
    }
}

# 2. Verifica Node.js / npx (necessário para servidores MCP como @azure-devops/mcp)
$hasNode = $false
if (Get-Command npx -ErrorAction SilentlyContinue) {
    $hasNode = $true
    Write-Host "  [OK] Node.js / npx detectado no PATH." -ForegroundColor Green
} else {
    Write-Host "Aviso: Node.js / npx não encontrado (necessário para servidores MCP como @azure-devops/mcp). Tentando instalar via winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        if (Get-Command npx -ErrorAction SilentlyContinue) {
            $hasNode = $true
            Write-Host "  [OK] Node.js LTS instalado com sucesso via winget." -ForegroundColor Green
        } else {
            Write-Host "Aviso: Node.js instalado mas npx ainda não está no PATH desta sessão. Reinicie o terminal após a instalação para usar MCPs baseados em Node." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Aviso: Node.js / npx não encontrado e winget não está disponível. Servidores MCP Node.js exigem Node.js instalado manualmente." -ForegroundColor Yellow
    }
}

# 3. Executa o provisionador total (Clonagem Vendor -> .venv -> Banco -> MCPs -> Docker -> Testes)
Write-Host "Executando script de provisionamento de ambiente..." -ForegroundColor Cyan
& python scripts/setup_environment.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "Falha durante o provisionamento total." -ForegroundColor Red
    exit $LASTEXITCODE
}

# 4. Criar o diretório de binários do usuário em $HOME\.agents_squad\bin se não existir
$userBinDir = Join-Path $HOME ".agents_squad\bin"
if (-not (Test-Path $userBinDir)) {
    New-Item -ItemType Directory -Force -Path $userBinDir | Out-Null
    Write-Host "  [OK] Diretório de binários criado em: $userBinDir" -ForegroundColor Green
}

# 5. Gerar o shim executável squad.cmd e agent-squad.cmd apontando para o Python do .venv e scripts/agent_squad.py
$venvPython = Join-Path $scriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
}
$agentSquadScript = Join-Path $scriptRoot "scripts\agent_squad.py"

$cmdContent = "@echo off`r`n`"%venvPython%`" `"%agentSquadScript%`" %*"
Set-Content -Path (Join-Path $userBinDir "squad.cmd") -Value $cmdContent -Encoding ASCII
Set-Content -Path (Join-Path $userBinDir "agent-squad.cmd") -Value $cmdContent -Encoding ASCII
Write-Host "  [OK] Shims squad.cmd e agent-squad.cmd gerados em $userBinDir" -ForegroundColor Green

# 6. Garantir que $HOME\.agents_squad\bin seja adicionado ao PATH da sessão e ao PATH de usuário do Windows
if ($env:PATH -notlike "*$userBinDir*") {
    $env:PATH = "$userBinDir;$env:PATH"
}

try {
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not $userPath) {
        $userPath = ""
    }
    if ($userPath -notlike "*$userBinDir*") {
        if ($userPath -and -not $userPath.EndsWith(";")) {
            $userPath = "$userPath;"
        }
        $newPath = "$userPath$userBinDir"
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        Write-Host "  [OK] $userBinDir adicionado ao PATH do usuário do Windows." -ForegroundColor Green
    } else {
        Write-Host "  [OK] $userBinDir já está presente no PATH do usuário." -ForegroundColor Green
    }
} catch {
    Write-Host "  [AVISO] Não foi possível atualizar o PATH de usuário automaticamente: $_" -ForegroundColor Yellow
}

Write-Host "Instalação finalizada com sucesso! Todos os serviços e ferramentas estão operacionais." -ForegroundColor Green
