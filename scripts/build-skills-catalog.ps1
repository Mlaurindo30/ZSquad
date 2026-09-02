param([string]$Root = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
$skillsRoot = Join-Path $Root 'skills'
$records = @()
$assignmentMap = @{}

Get-ChildItem -LiteralPath (Join-Path $Root 'agents') -Recurse -Filter manifest.yaml | ForEach-Object {
    $manifest = Get-Content -LiteralPath $_.FullName -Raw
    if ($manifest -notmatch '(?m)^agent:\s*([a-z0-9-]+)\s*$') { return }
    $agent = $Matches[1]
    if ($manifest -notmatch '(?ms)^assigned:\s*\r?\n(.*?)^discovery:') { return }
    [regex]::Matches($Matches[1], '(?m)^\s+- path:\s*(skills/[^\r\n]+)') | ForEach-Object {
        $path = $_.Groups[1].Value.Trim()
        if (-not $assignmentMap.ContainsKey($path)) {
            $assignmentMap[$path] = [System.Collections.Generic.List[string]]::new()
        }
        if (-not $assignmentMap[$path].Contains($agent)) { $assignmentMap[$path].Add($agent) }
    }
}

Get-ChildItem -LiteralPath $skillsRoot -Recurse -Filter SKILL.md | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($Root, $_.Directory.FullName).Replace('\','/')
    if ($relative -like 'skills/discovery/intake/*' -or $relative -like 'skills/discovery/quarantine/*') { return }
    $parts = $relative.Split('/')
    $domain = if ($parts.Count -gt 1) { $parts[1] } else { 'unknown' }
    $specialization = if ($parts.Count -gt 2) { $parts[2] } else { 'general' }
    $text = Get-Content -LiteralPath $_.FullName -Raw
    $name = if ($text -match '(?m)^name:\s*["'']?([^\r\n"'']+)') { $Matches[1].Trim() } else { $_.Directory.Name }
    $description = if ($text -match '(?m)^description:\s*["'']?([^\r\n"'']+)') { $Matches[1].Trim() } else { 'Sem descrição no frontmatter.' }
    $description = $description.Replace("'", "''")
    $folder = $_.Directory.FullName
    [string[]]$hashFiles = Get-ChildItem -LiteralPath $folder -File -Recurse | ForEach-Object {
        [IO.Path]::GetRelativePath($folder, $_.FullName).Replace('\','/')
    }
    [Array]::Sort($hashFiles, [StringComparer]::Ordinal)
    $hashLines = $hashFiles | ForEach-Object {
        $fileRelative = $_
        $fileHash = (Get-FileHash -LiteralPath (Join-Path $folder $fileRelative) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$fileRelative=$fileHash"
    }
    $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes(($hashLines -join "`n")))
        $hash = -join ($hashBytes | ForEach-Object { $_.ToString('x2') })
    } finally {
        $hashAlgorithm.Dispose()
    }
    $provenance = switch -Wildcard ($relative) {
        'skills/data/databricks/*' { @('databricks-agent-skills','verified-third-party','Apache-2.0'); break }
        'skills/security/security-review' { @('getsentry-skills','verified-third-party','Apache-2.0'); break }
        'skills/security/gha-security-review' { @('getsentry-skills','verified-third-party','Apache-2.0'); break }
        'skills/delivery/superpowers/*' { @('obra-superpowers','verified-third-party','MIT'); break }
        'skills/engineering/ui-ux/*' { @('ui-ux-pro-max-and-local-curation','mixed-local-and-third-party','internal-use'); break }
        'skills/delivery/token-efficiency/caveman' { @('juliusbrussee-caveman','verified-third-party','MIT'); break }
        'skills/security/owasp-security' { @('agamm-owasp','verified-third-party','MIT'); break }
        'skills/delivery/orchestration/orchestrate-sdlc-gates' { @('squad-internal','internal-original','proprietary'); break }
        'skills/delivery/orchestration/govern-agent-handoffs' { @('squad-internal','internal-original','proprietary'); break }
        'skills/delivery/scrum-kanban/operate-scrum-kanban' { @('squad-internal','internal-original','proprietary'); break }
        'skills/requirements/refine-requirements-stories' { @('squad-internal','internal-original','proprietary'); break }
        'skills/architecture/design-evidence-architecture' { @('squad-internal','internal-original','proprietary'); break }
        'skills/engineering/code-documentation/clean-code-contract' { @('squad-internal','internal-original','proprietary'); break }
        'skills/security/security-review-gates' { @('squad-internal','internal-original','proprietary'); break }
        'skills/ai/ai-engineering/ai-engineering' { @('squad-internal','internal-original','proprietary'); break }
        'skills/ai/ai-analysis/ai-analysis' { @('squad-internal','internal-original','proprietary'); break }
        'skills/data/mlflow/*' { @('user-provided-specialization','user-local-import','internal-use'); break }
        default { @('user-local-curation','user-local-import','internal-use') }
    }
    $assignedTo = if ($assignmentMap.ContainsKey($relative)) { @($assignmentMap[$relative] | Sort-Object) } else { @() }
    $records += [pscustomobject]@{name=$name; path=$relative; domain=$domain; specialization=$specialization; source=$provenance[0]; provenance=$provenance[1]; license_status=$provenance[2]; assigned_to=$assignedTo; hash=$hash; description=$description}
}

$lines = @(
    'version: 2',
    'mode: local-curated',
    "generated_at: '$((Get-Date).ToString('o'))'",
    "active_skill_count: $($records.Count)",
    'catalog:'
)
foreach ($record in $records | Sort-Object domain,specialization,name,path) {
    $lines += "  - name: $($record.name)"
    $lines += "    path: $($record.path)"
    $lines += "    domain: $($record.domain)"
    $lines += "    specialization: $($record.specialization)"
    $lines += "    source: $($record.source)"
    $lines += "    provenance: $($record.provenance)"
    $lines += "    license_status: $($record.license_status)"
    $lines += "    assigned_to: [$($record.assigned_to -join ', ')]"
    $lines += "    load: on-demand"
    $lines += "    folder_sha256: $($record.hash)"
    $lines += "    description: '$($record.description)'"
}
$lines += 'policy:'
$lines += '  discovery: config/discovery-policy.yaml'
$lines += '  sources: skills/SOURCES.md'
$lines += '  quarantine_is_not_loadable: true'
$lines += '  every_active_skill_requires_assigned_agent: true'

Set-Content -LiteralPath (Join-Path $Root 'config/skills-catalog.yaml') -Value $lines -Encoding utf8
"Cataloged $($records.Count) active skills."
