<#
.SYNOPSIS
    Instalador e Provisionador Total Zero-to-Hero para Agents Squad no Windows.
.DESCRIPTION
    Verifica pre-requisitos (Git, Python 3.11+, Node.js >= 18, npm, npx),
    provisiona ambiente virtual isolado (.venv), instala dependencias,
    inicializa banco local SQLite (banco/squad.db), sincroniza repositorios
    vendor e MCPs, gera shims executaveis (squad.cmd e agent-squad.cmd) apontando
    para o venv do runtime, e atualiza o PATH do usuario.
.PARAMETER Clean
    Forca limpeza do .venv e banco local para reinstalacao completa do zero.
.PARAMETER SkipDocker
    Ignora inicializacao de containers Docker durante o provisionamento.
.PARAMETER SkipTests
    Ignora execucao da suite de testes durante o provisionamento.
#>
param(
    [switch]$Clean,
    [switch]$SkipDocker,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "   AGENTS SQUAD — INSTALADOR TOTAL ZERO-TO-HERO        " -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

$scriptRoot = $PSScriptRoot
Set-Location $scriptRoot

# 0. Suporte a instalacao limpa (-Clean)
if ($Clean) {
    Write-Host "Executando limpeza de ambiente (-Clean)..." -ForegroundColor Yellow
    $venvPath = Join-Path $scriptRoot ".venv"
    if (Test-Path $venvPath) {
        Remove-Item -Recurse -Force $venvPath -ErrorAction SilentlyContinue
        Write-Host "  [OK] .venv anterior removido." -ForegroundColor Green
    }
    $dbPath = Join-Path $scriptRoot "banco\squad.db"
    if (Test-Path $dbPath) {
        Remove-Item -Force $dbPath -ErrorAction SilentlyContinue
        Remove-Item -Force "$dbPath-wal" -ErrorAction SilentlyContinue
        Remove-Item -Force "$dbPath-shm" -ErrorAction SilentlyContinue
        Write-Host "  [OK] banco/squad.db limpo." -ForegroundColor Green
    }
}

# 1. Verifica Git
Write-Host "Verificando pre-requisitos do sistema..." -ForegroundColor Cyan
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "Erro: Git nao foi encontrado no PATH. O Git e obrigatorio para clonagem e sincronizacao de repositorios vendor." -ForegroundColor Red
    exit 1
}
try {
    $gitVer = & git --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Erro: Git falhou ao relatar versao: $gitVer" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] $gitVer detectado no PATH." -ForegroundColor Green
} catch {
    Write-Host "Erro ao executar git: $_" -ForegroundColor Red
    exit 1
}

# 2. Verifica Python 3.11+ com separacao estrita de launcher ($pythonExe e $pythonArgs)
$pythonExe = "python"
$pythonArgs = @()
$hasPython = $false

try {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $pyVerOut = & python --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $pyVerOut -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 11) {
                $pythonExe = "python"
                $pythonArgs = @()
                $hasPython = $true
                Write-Host "  [OK] Python $major.$minor detectado no PATH (python)." -ForegroundColor Green
            }
        }
    }
} catch {}

if (-not $hasPython) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            foreach ($v in @("-3.13", "-3.12", "-3.11", "-3")) {
                $pyVerOut = & py $v --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $pyVerOut -match "Python (\d+)\.(\d+)") {
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    if ($major -eq 3 -and $minor -ge 11) {
                        $pythonExe = "py"
                        $pythonArgs = @($v)
                        $hasPython = $true
                        Write-Host "  [OK] Python $major.$minor detectado via py launcher ($v)." -ForegroundColor Green
                        break
                    }
                }
            }
        } catch {}
    }
}

if (-not $hasPython) {
    Write-Host "Aviso: Python 3.11+ nao encontrado. Tentando instalar via winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        try {
            $pyVerOut = & python --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $pyVerOut -match "Python (\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -eq 3 -and $minor -ge 11) {
                    $pythonExe = "python"
                    $pythonArgs = @()
                    $hasPython = $true
                    Write-Host "  [OK] Python $major.$minor instalado com sucesso via winget." -ForegroundColor Green
                }
            }
        } catch {}
    }
}

if (-not $hasPython) {
    Write-Host "Erro: Python 3.11+ nao encontrado e nao pode ser instalado automaticamente. Instale Python 3.11+ manualmente." -ForegroundColor Red
    exit 1
}

# 3. Verifica Node.js >= 18, npm e npx (obrigatorio para os 7 vendors upstream, esp. sdlc-agents e trace-mcp)
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-Host "Aviso: Node.js nao encontrado no PATH. Tentando instalar via winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
        $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    }
}

if (-not $nodeCmd) {
    Write-Host "Erro: Node.js nao encontrado no PATH. Node.js >= 18 e obrigatorio para integracoes upstream canonicas (sdlc-agents e trace-mcp). Instale Node.js LTS (>= 18) manualmente." -ForegroundColor Red
    exit 1
}

try {
    $nodeVerOutput = & node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Erro ao executar node --version: $nodeVerOutput" -ForegroundColor Red
        exit 1
    }
    if ($nodeVerOutput -match "v(\d+)\.") {
        $nodeMajor = [int]$Matches[1]
        if ($nodeMajor -lt 18) {
            Write-Host "Erro: Node.js $nodeVerOutput detectado. Requer-se Node.js >= 18 para as integracoes upstream canonicas (sdlc-agents e trace-mcp)." -ForegroundColor Red
            exit 1
        }
        Write-Host "  [OK] Node.js $nodeVerOutput detectado (>= 18)." -ForegroundColor Green
    } else {
        Write-Host "Erro: Nao foi possivel determinar a versao do Node.js a partir de: $nodeVerOutput" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "Erro ao validar versao do Node.js: $_" -ForegroundColor Red
    exit 1
}

# 3.1 Verifica npm
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Host "Erro: npm nao foi encontrado no PATH. npm e obrigatorio para gerenciamento de pacotes Node.js." -ForegroundColor Red
    exit 1
}
try {
    $npmVer = & npm --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Erro ao executar npm --version: $npmVer" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] npm $npmVer detectado." -ForegroundColor Green
} catch {
    Write-Host "Erro ao validar npm: $_" -ForegroundColor Red
    exit 1
}

# 3.2 Verifica npx
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if (-not $npxCmd) {
    Write-Host "Erro: npx nao foi encontrado no PATH. npx e obrigatorio para servidores MCP baseados em Node." -ForegroundColor Red
    exit 1
}
try {
    $npxVer = & npx --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Erro ao executar npx --version: $npxVer" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] npx $npxVer detectado." -ForegroundColor Green
} catch {
    Write-Host "Erro ao validar npx: $_" -ForegroundColor Red
    exit 1
}

# 4. Executa o provisionador total passando exatamente $pythonExe e $pythonArgs
Write-Host "Executando script de provisionamento de ambiente..." -ForegroundColor Cyan
$setupScript = Join-Path $scriptRoot "scripts\setup_environment.py"
$setupArgs = @()
if ($pythonArgs -and $pythonArgs.Count -gt 0) {
    $setupArgs += $pythonArgs
}
$setupArgs += $setupScript
if ($SkipDocker) {
    $setupArgs += "--skip-docker"
}
if ($SkipTests) {
    $setupArgs += "--skip-tests"
}

& $pythonExe @setupArgs
$setupExitCode = $LASTEXITCODE

if ($setupExitCode -ne 0) {
    Write-Host "Falha durante o provisionamento total (codigo de saida: $setupExitCode)." -ForegroundColor Red
    exit $setupExitCode
}

# 5. Criar diretorio de binarios do usuario em $HOME\.agents_squad\bin se nao existir
$userBinDir = Join-Path $HOME ".agents_squad\bin"
if (-not (Test-Path $userBinDir)) {
    New-Item -ItemType Directory -Force -Path $userBinDir | Out-Null
    Write-Host "  [OK] Diretorio de binarios criado em: $userBinDir" -ForegroundColor Green
}

# 5.1 Registrar runtime ativo no registro global do usuario ($HOME\.agents_squad\config\active_runtime.json)
$userConfigDir = Join-Path $HOME ".agents_squad\config"
if (-not (Test-Path $userConfigDir)) {
    New-Item -ItemType Directory -Force -Path $userConfigDir | Out-Null
}
$activeRuntimeFile = Join-Path $userConfigDir "active_runtime.json"
$activeRuntimeData = @{ runtime = $scriptRoot } | ConvertTo-Json
Set-Content -Path $activeRuntimeFile -Value $activeRuntimeData -Encoding UTF8
Write-Host "  [OK] Registro de runtime ativo gravado em: $activeRuntimeFile" -ForegroundColor Green

# 6. Gerar os shims executaveis dinamicos squad.cmd e agent-squad.cmd
$fallbackPythonCmd = if ($pythonArgs.Count -gt 0) {
    if ($pythonExe -match " ") { "`"$pythonExe`" $($pythonArgs -join ' ')" } else { "$pythonExe $($pythonArgs -join ' ')" }
} else {
    if ($pythonExe -match " ") { "`"$pythonExe`"" } else { $pythonExe }
}

$shimLines = @(
    "@echo off",
    "setlocal",
    "",
    ":: 1. Prioridade 1: Variavel de ambiente SQUAD_RUNTIME explicita",
    "if not `"%SQUAD_RUNTIME%`"`=`=`"`" goto :resolve_python",
    "",
    ":: 2. Prioridade 2: Auto-descoberta relativa ao diretorio do shim",
    "if exist `"%~dp0scripts\agent_squad.py`" (",
    "    set `"SQUAD_RUNTIME=%~dp0`"",
    "    goto :resolve_python",
    ")",
    "if exist `"%~dp0..\scripts\agent_squad.py`" (",
    "    for /f `"tokens=*`" %%i in (`"%~dp0..`") do set `"SQUAD_RUNTIME=%%~fi`"",
    "    goto :resolve_python",
    ")",
    "if exist `"%~dp0..\..\scripts\agent_squad.py`" (",
    "    for /f `"tokens=*`" %%i in (`"%~dp0..\..`") do set `"SQUAD_RUNTIME=%%~fi`"",
    "    goto :resolve_python",
    ")",
    "",
    ":: 3. Prioridade 3: Se executado dentro da raiz do runtime",
    "if exist `"%CD%\scripts\agent_squad.py`" (",
    "    set `"SQUAD_RUNTIME=%CD%`"",
    "    goto :resolve_python",
    ")",
    "",
    ":: 4. Prioridade 4: Registro global sem dependencia de python global",
    "if exist `"%USERPROFILE%\.agents_squad\config\active_runtime.json`" (",
    "    for /f `"usebackq delims=`" %%i in (``powershell -NoProfile -ExecutionPolicy Bypass -Command `"try { (Get-Content -Raw '%USERPROFILE%\.agents_squad\config\active_runtime.json' | ConvertFrom-Json).runtime } catch {}`" 2^>nul``) do (",
    "        if not `"%%i`"`=`=`"`" set `"SQUAD_RUNTIME=%%i`"",
    "    )",
    ")",
    "",
    ":resolve_python",
    ":: Strip trailing backslash if present",
    "if not `"%SQUAD_RUNTIME%`"`=`=`"`" (",
    "    if `"%SQUAD_RUNTIME:~-1%`"`=`=`"\`" set `"SQUAD_RUNTIME=%SQUAD_RUNTIME:~0,-1%`"",
    ")",
    "",
    "if `"%SQUAD_RUNTIME%`"`=`=`"`" (",
    "    echo Error: SQUAD_RUNTIME could not be resolved. Please set SQUAD_RUNTIME environment variable.",
    "    endlocal & exit /b 1",
    ")",
    "",
    "if not exist `"%SQUAD_RUNTIME%\scripts\agent_squad.py`" (",
    "    echo Error: Invalid SQUAD_RUNTIME at `"%SQUAD_RUNTIME%`". File scripts\agent_squad.py not found.",
    "    endlocal & exit /b 1",
    ")",
    "",
    ":: Execucao do Python: priorizar venv do runtime, senao fallback detectado no install",
    "if exist `"%SQUAD_RUNTIME%\.venv\Scripts\python.exe`" (",
    "    `"%SQUAD_RUNTIME%\.venv\Scripts\python.exe`" `"%SQUAD_RUNTIME%\scripts\agent_squad.py`" %*",
    ") else (",
    "    $fallbackPythonCmd `"%SQUAD_RUNTIME%\scripts\agent_squad.py`" %*",
    ")",
    "",
    "endlocal & exit /b %ERRORLEVEL%"
)
$cmdContent = $shimLines -join "`r`n"

# Gravar shims exclusivamente no diretorio de binarios do usuario ($HOME\.agents_squad\bin)
Set-Content -Path (Join-Path $userBinDir "squad.cmd") -Value $cmdContent -Encoding ASCII
Set-Content -Path (Join-Path $userBinDir "agent-squad.cmd") -Value $cmdContent -Encoding ASCII

# Limpeza defensiva de shims na raiz do repositorio para manter o git status limpo
$rootSquadCmd = Join-Path $scriptRoot "squad.cmd"
if (Test-Path $rootSquadCmd) {
    Remove-Item -Force $rootSquadCmd -ErrorAction SilentlyContinue
}
$rootAgentSquadCmd = Join-Path $scriptRoot "agent-squad.cmd"
if (Test-Path $rootAgentSquadCmd) {
    Remove-Item -Force $rootAgentSquadCmd -ErrorAction SilentlyContinue
}

Write-Host "  [OK] Shims squad.cmd e agent-squad.cmd gerados com sucesso em: $userBinDir" -ForegroundColor Green

# 7. Garantir que $HOME\.agents_squad\bin seja adicionado ao PATH da sessao e do usuario
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
        Write-Host "  [OK] $userBinDir adicionado ao PATH do usuario do Windows." -ForegroundColor Green
    } else {
        Write-Host "  [OK] $userBinDir ja esta presente no PATH do usuario." -ForegroundColor Green
    }
} catch {
    Write-Host "  [AVISO] Nao foi possivel atualizar o PATH de usuario automaticamente: $_" -ForegroundColor Yellow
}

Write-Host "Instalacao finalizada com sucesso! Todos os servicos e ferramentas estao operacionais." -ForegroundColor Green
exit 0
