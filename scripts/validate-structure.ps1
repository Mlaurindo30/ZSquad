[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$errors = [System.Collections.Generic.List[string]]::new()

function Require-File([string]$relative) {
    $path = Join-Path $root $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $errors.Add("missing file: $relative") }
}

@('AGENTS.md','CLAUDE.md','GEMINI.md','README.md','config/agent-registry.yaml','config/workflow.yaml','config/memory.yaml','config/discovery-policy.yaml','config/skills-catalog.yaml','contracts/handoff.schema.json','contracts/gate-decision.schema.json','contracts/memory-delta.schema.json','contracts/work-item.schema.json','docs/01-proposta-arquitetura.md','docs/02-metodologia-e-fluxo.md','docs/03-catalogo-de-agentes.md','docs/04-catalogo-de-skills.md','docs/05-memoria-handoffs-e-specs.md','docs/06-roadmap-de-implantacao.md','docs/07-operacao-e-integracoes.md','docs/08-padroes-de-codigo.md','work/README.md') | ForEach-Object { Require-File $_ }

$agentsContent = Get-Content -LiteralPath (Join-Path $root 'AGENTS.md') -Raw
$claudeContent = Get-Content -LiteralPath (Join-Path $root 'CLAUDE.md') -Raw -ErrorAction SilentlyContinue
$geminiContent = Get-Content -LiteralPath (Join-Path $root 'GEMINI.md') -Raw -ErrorAction SilentlyContinue
if ($agentsContent -and $agentsContent.Length -gt 12000) { $errors.Add("AGENTS.md exceeds 12,000 character budget") }
if ($claudeContent -and $claudeContent.Length -gt 40000) { $errors.Add("CLAUDE.md exceeds 40,000 character budget") }
if ($geminiContent -and $geminiContent.Length -gt 12000) { $errors.Add("GEMINI.md exceeds 12,000 character budget") }

foreach ($schema in @('contracts/handoff.schema.json','contracts/gate-decision.schema.json','contracts/memory-delta.schema.json','contracts/work-item.schema.json')) {
    try { $null = Get-Content -LiteralPath (Join-Path $root $schema) -Raw | ConvertFrom-Json }
    catch { $errors.Add("invalid JSON: $schema — $($_.Exception.Message)") }
}

$registry = Get-Content -LiteralPath (Join-Path $root 'config/agent-registry.yaml') -Raw
$agentIds = [regex]::Matches($registry, '(?m)^\s*- id:\s*([a-z0-9-]+)\s*$') | ForEach-Object { $_.Groups[1].Value }
if ($agentIds.Count -lt 30) { $errors.Add("expected at least 30 registered agents, found $($agentIds.Count)") }

foreach ($id in $agentIds) {
    $path = [regex]::Match($registry, "(?ms)- id:\s*$id\s*\r?\n\s+path:\s*([^\r\n]+)").Groups[1].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($path)) { $errors.Add("agent $id has no path"); continue }
    $prompt = Join-Path $root "$path/PROMPT.md"
    $manifest = Join-Path $root "$path/skills/manifest.yaml"
    $native = Join-Path $root "$path/skills/native"
    if (-not (Test-Path -LiteralPath $prompt -PathType Leaf)) { $errors.Add("missing prompt: $path/PROMPT.md") }
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { $errors.Add("missing manifest: $path/skills/manifest.yaml") }
    if (-not (Test-Path -LiteralPath $native -PathType Container)) { $errors.Add("missing native skills: $path/skills/native") }
    if (Test-Path -LiteralPath $prompt) {
        $raw = Get-Content -LiteralPath $prompt -Raw
        $sectionPairs = @(
            @('## Mission','## Missão'),
            @('## Exclusive Responsibilities','## Responsabilidades exclusivas'),
            @('## Deliverables','## Entregáveis'),
            @('## Mandatory Protocol','## Protocolo obrigatório'),
            @('## Boundaries','## Limites'),
            @('## Role Heuristics','## Heurísticas do papel'),
            @('## When to Load Which Skill','## Quando carregar qual skill')
        )
        foreach ($pair in $sectionPairs) {
            $found = $false
            foreach ($section in $pair) {
                if ($raw -match [regex]::Escape($section)) { $found = $true; break }
            }
            if (-not $found) { $errors.Add("prompt missing section $($pair -join ' or '): $id") }
        }
        if ($raw -match '\[TODO:|TODO:') { $errors.Add("TODO placeholder in prompt: $id") }
    }
    if (Test-Path -LiteralPath $manifest) {
        $raw = Get-Content -LiteralPath $manifest -Raw
        if ($raw -notmatch '(?m)^\s*- path:\s+agents/') { $errors.Add("manifest missing native path: $id") }
        if ($raw -match 'skills/discovery/(intake|quarantine)') { $errors.Add("manifest points to non-runtime discovery content: $id") }
    }
}

$activeSkills = Get-ChildItem -LiteralPath (Join-Path $root 'skills') -Recurse -Filter SKILL.md | Where-Object { $_.FullName -notmatch '\\skills\\discovery\\(intake|quarantine)\\' }
if ($activeSkills.Count -lt 100) { $errors.Add("expected at least 100 curated active skills, found $($activeSkills.Count)") }
foreach ($skill in $activeSkills) {
    $raw = Get-Content -LiteralPath $skill.FullName -Raw
    if ($raw -notmatch '(?m)^name:\s*["'']?[a-z0-9-]+["'']?\s*$') { $errors.Add("invalid skill name: $($skill.FullName)") }
    if ($raw -notmatch '(?m)^description:\s*\S+') { $errors.Add("invalid skill description: $($skill.FullName)") }
}

$forbidden = Get-ChildItem -LiteralPath $root -Recurse -File -Include *.md,*.yaml,*.json | Where-Object { $_.FullName -notmatch '\\skills\\' } | Select-String -Pattern 'comg[aá]s|dados-ia-agent-corporativo|skills/vendor|project_core|project_local|user_global|vendor_external|```mermaid' -CaseSensitive:$false
if ($forbidden) { $errors.Add("forbidden legacy/source references: $($forbidden -join '; ')") }

if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }
Write-Output "VALID structure agents=$($agentIds.Count) active_skills=$($activeSkills.Count) schemas=4"
