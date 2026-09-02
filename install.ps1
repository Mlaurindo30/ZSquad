# PowerShell Setup Script - Agents Squad (Instalação e Provisionamento Total)
$ErrorActionPreference = "Stop"

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "   AGENTS SQUAD — INSTALADOR TOTAL ZERO-TO-HERO        " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$scriptRoot = $PSScriptRoot
Set-Location $scriptRoot

# 1. Verifica interpretador Python base
$basePython = "python"
try {
    & $basePython --version
} catch {
    Write-Host "Erro: Python 3 não encontrado no PATH do sistema." -ForegroundColor Red
    exit 1
}

# 2. Executa o provisionador total (Clonagem Vendor -> .venv -> Banco -> MCPs -> Docker -> Testes)
& $basePython scripts/setup_environment.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "Falha durante o provisionamento total." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "Instalação finalizada com sucesso! Todos os serviços e ferramentas estão operacionais." -ForegroundColor Green
