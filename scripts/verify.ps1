[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    & "$root/scripts/validate-structure.ps1"

    & python -B "$root/scripts/normalize-skill-frontmatter.py" --check
    if ($LASTEXITCODE -ne 0) { throw 'skill frontmatter normalization check failed' }

    $validator = Join-Path $env:USERPROFILE '.codex/skills/.system/skill-creator/scripts/quick_validate.py'
    $skills = Get-ChildItem -LiteralPath "$root/skills" -Recurse -Filter SKILL.md |
        Where-Object { $_.FullName -notmatch '\\skills\\discovery\\(intake|quarantine)\\' }
    $nativeSkills = Get-ChildItem -LiteralPath "$root/agents" -Recurse -Filter SKILL.md |
        Where-Object { $_.FullName -match '\\skills\\native\\' }
    if (Test-Path -LiteralPath $validator) {
        $env:SQUAD_SKILL_VALIDATOR = $validator
        @'
import importlib.util, os, pathlib
validator_path = pathlib.Path(os.environ['SQUAD_SKILL_VALIDATOR'])
spec = importlib.util.spec_from_file_location('squad_quick_validate', validator_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = pathlib.Path('.')
skills = [p.parent for p in (root/'skills').rglob('SKILL.md') if '/discovery/intake/' not in p.as_posix() and '/discovery/quarantine/' not in p.as_posix()]
skills += [p.parent for p in (root/'agents').rglob('SKILL.md') if '/skills/native/' in p.as_posix()]
failures = []
for skill in skills:
    valid, message = module.validate_skill(skill)
    if not valid:
        failures.append(f'{skill}: {message}')
if failures:
    raise SystemExit('\n'.join(failures))
print(f'SKILL_CONTRACT_OK files={len(skills)}')
'@ | python -B -
        Remove-Item Env:SQUAD_SKILL_VALIDATOR
        if ($LASTEXITCODE -ne 0) { throw 'skill contract validation failed' }
    } else {
        Write-Warning "quick_validate.py não encontrado; validação de contrato local continua ativa."
    }

    @'
import pathlib, yaml, json, hashlib
root = pathlib.Path('.')
for path in root.rglob('*.yaml'):
    text = path.read_text(encoding='utf-8')
    list(yaml.safe_load_all(text))
for path in (root/'contracts').glob('*.json'):
    json.loads(path.read_text(encoding='utf-8'))
cat = yaml.safe_load((root/'config/skills-catalog.yaml').read_text(encoding='utf-8'))
assert cat['mode'] == 'local-curated'
catalog_paths = {item['path'] for item in cat['catalog']}
assert all(item.get('assigned_to') for item in cat['catalog']), 'active skill without assigned agent'
for item in cat['catalog']:
    folder = root/item['path']
    lines = []
    files = [path for path in folder.rglob('*') if path.is_file()]
    for file in sorted(files, key=lambda path: path.relative_to(folder).as_posix()):
        relative = file.relative_to(folder).as_posix()
        lines.append(f'{relative}={hashlib.sha256(file.read_bytes()).hexdigest()}')
    resolved = hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()
    assert resolved == item['folder_sha256'], f'stale skill checksum: {item["path"]}'
for agent_path in (root/'agents').glob('[0-9][0-9]-*'):
    manifest = yaml.safe_load((agent_path/'skills'/'manifest.yaml').read_text(encoding='utf-8'))
    for section in ('native','assigned'):
        for item in manifest.get(section, []):
            p = pathlib.Path(item['path'])
            if section == 'native':
                assert p.parts[0] == 'agents', (agent_path.name, item)
                assert (root/p).exists(), (agent_path.name, item)
            else:
                assert p.as_posix() in catalog_paths, (agent_path.name, item)
                assert (root/p/'SKILL.md').exists(), (agent_path.name, item)
    assert manifest['discovery']['maximum_loaded'] <= 3
print(f'CONFIG_RESOLUTION_OK agents={len(list((root/"agents").glob("[0-9][0-9]-*")))} skills={len(catalog_paths)}')
'@ | python -B -
    if ($LASTEXITCODE -ne 0) { throw 'configuration resolution failed' }

    & python -B "$root/scripts/agent_squad.py" audit
    if ($LASTEXITCODE -ne 0) { throw 'agent squad audit failed' }

    & python -B -m unittest discover -s "$root/scripts/tests" -v
    if ($LASTEXITCODE -ne 0) { throw 'agent squad tests failed' }

    @'
import pathlib, re, urllib.parse, yaml
root = pathlib.Path('.')
broken = []
for skill in (root/'skills').rglob('SKILL.md'):
    if '/discovery/intake/' in skill.as_posix() or '/discovery/quarantine/' in skill.as_posix():
        continue
    raw = skill.read_text(encoding='utf-8', errors='replace')
    frontmatter = yaml.safe_load(re.match(r'^---\n(.*?)\n---', raw, re.S).group(1))
    if skill.parent.name != frontmatter['name']:
        broken.append(f'folder/name mismatch: {skill.parent.relative_to(root)} != {frontmatter["name"]}')
    for markdown in [skill, *skill.parent.rglob('*.md')]:
        text = markdown.read_text(encoding='utf-8', errors='replace')
        text = re.sub(r'```.*?```', '', text, flags=re.S)
        for match in re.finditer(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', text):
            value = match.group(1).strip().split(' ', 1)[0].strip('<>')
            if not value or value.startswith(('#','http://','https://','mailto:','data:')):
                continue
            target = urllib.parse.unquote(value.split('#', 1)[0])
            if target and not (markdown.parent/target).exists():
                broken.append(f'broken local link: {markdown.relative_to(root)} -> {value}')
if broken:
    raise SystemExit('\n'.join(sorted(set(broken))))
print('SKILL_LINKS_OK')
'@ | python -B -
    if ($LASTEXITCODE -ne 0) { throw 'skill local link validation failed' }

    @'
import pathlib
root = pathlib.Path('.')
bad = []
for path in list((root/'agents').rglob('*')) + list((root/'templates').glob('*.yaml')) + list((root/'contracts').glob('*.json')):
    if path.is_file() and any(b < 32 and b not in (9, 10, 13) for b in path.read_bytes()):
        bad.append(str(path))
if bad:
    raise SystemExit('CONTROL_CHARS: ' + ', '.join(bad))
print('CONTROL_CHAR_SCAN_OK')
'@ | python -B -
    if ($LASTEXITCODE -ne 0) { throw 'control character scan failed' }

    $placeholders = Get-ChildItem -LiteralPath $root -Recurse -File -Include *.md,*.yaml,*.json |
        Where-Object { $_.FullName -notmatch '\\scripts\\|\\skills\\|\\agents\\[0-9][0-9]-[^\\]+\\skills\\|\\integrations\\vendor\\' } |
        Select-String -Pattern '\[TODO:|\[PLACEHOLDER\]|<YOUR_' -SimpleMatch:$false
    if ($placeholders) { throw "placeholder scan failed: $($placeholders -join '; ')" }

    Write-Output "VERIFY_OK active_skill_files=$($skills.Count)"
} finally { Pop-Location }
