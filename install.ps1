param(
    [ValidateSet('auto','generic','codex','claude-code','claude-desktop','cursor')][string]$Client = 'auto',
    [string]$Project = '.',
    [string]$Blender = '',
    [string]$SkillDir = '',
    [string]$Source = 'https://github.com/arabianguy08/companion-animation-pipeline/archive/refs/tags/v0.1.1.zip',
    [switch]$ManagedBlender,
    [switch]$NoSkill
)
$ErrorActionPreference = 'Stop'
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvPath = $uvCommand.Source
} else {
    $bootstrapHome = if ($env:COMPANION_ANIMATION_HOME) { $env:COMPANION_ANIMATION_HOME } else { Join-Path $env:LOCALAPPDATA 'companion-animation' }
    $env:UV_INSTALL_DIR = Join-Path $bootstrapHome 'tools'
    $env:UV_NO_MODIFY_PATH = '1'
    $uvInstaller = Invoke-RestMethod 'https://astral.sh/uv/0.9.7/install.ps1'
    & ([scriptblock]::Create($uvInstaller))
    $uvPath = Join-Path $env:UV_INSTALL_DIR 'uv.exe'
    if (!(Test-Path -LiteralPath $uvPath)) { throw 'uv installation did not produce an executable' }
}
& $uvPath python install 3.12 --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed' }
& $uvPath tool install --python 3.12 --force $Source
if ($LASTEXITCODE -ne 0) { throw 'Pipeline and MCP dependency installation failed' }
$toolBin = & $uvPath tool dir --bin
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate installed command' }
$runner = Join-Path $toolBin 'companion-animation.exe'
$setupArgs = @('setup','--client',$Client,'--project',$Project)
if ($Blender) { $setupArgs += @('--blender',$Blender) }
if ($SkillDir) { $setupArgs += @('--skill-dir',$SkillDir) }
if ($ManagedBlender) { $setupArgs += '--managed-blender' }
if ($NoSkill) { $setupArgs += '--no-skill' }
& $runner @setupArgs
if ($LASTEXITCODE -ne 0) { throw 'Setup failed; existing models and AI configurations were not replaced' }
Write-Host "Installed. Runner: $runner"
Write-Host 'Restart your AI client to load the new MCP server and skill.'
