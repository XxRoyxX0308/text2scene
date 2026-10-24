# text2scene orchestrator. Every stage is also a plain script you can run on its own (see AGENT.md).
#
#   .\pipeline\run.ps1 <stage> [-Project <dir>] [-Ids a,b] [-Retexture] [-Shots]
#
#   images     FLUX candidates for every prop / decal (batch mode) + contact sheets      (WSL, GPU)
#   models     background removal of the picks in work\selection.json + TRELLIS         (WSL, GPU)
#   materials  CC0 scans / flat materials -> export\materials
#   decals     picked decal images -> export\decals
#   layout     layout.json -> export\level.json, kit_needed.json, budget.json
#   kit        layout, then the modular kit in headless Blender -> export\kit
#   props      headless Blender batch bake of props -> export\props + bake_report.md (-Retexture, -Ids)
#   engine     import export\ into the engine named in assets.json "target" and build the level (-Shots)
#   build      shared + materials + decals + kit + props + layout + engine (everything after the 3D models)
#
# Tool locations come from t2s.config.json next to this repo's README (copy examples\t2s.config.json).
param(
    [Parameter(Mandatory)][ValidateSet('images', 'models', 'materials', 'decals', 'layout', 'kit', 'props', 'engine', 'build')]
    [string]$Stage,
    [string]$Project = (Get-Location).Path,
    [string[]]$Ids = @(),
    [switch]$Retexture,
    [switch]$Shots
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$pipe = $PSScriptRoot
$Project = (Resolve-Path $Project).Path
$cfgPath = Join-Path $repo 't2s.config.json'
if (-not (Test-Path $cfgPath)) { throw "missing $cfgPath (copy examples\t2s.config.json and fill in the paths)" }
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$assets = Get-Content (Join-Path $Project 'assets.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$engine = if ($assets.target) { $assets.target.engine } else { 'unreal' }
$export = Join-Path $Project 'export'

function Wsl([string]$p) { '/mnt/' + $p.Substring(0, 1).ToLower() + ($p.Substring(2) -replace '\\', '/') }
function Run([string]$exe, [string[]]$argList) {
    Write-Host "> $exe $($argList -join ' ')" -ForegroundColor DarkGray
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "$exe exited with $LASTEXITCODE" }
}
function Blender([string]$script, [string[]]$extra) {
    Run $cfg.blender (@('-b', '--factory-startup', '-P', (Join-Path $pipe $script), '--', $Project) + $extra)
}

$projWsl = Wsl $Project
$pipeWsl = Wsl $pipe
$conda = 'source ~/miniconda3/etc/profile.d/conda.sh'

switch ($Stage) {
    'images' {
        wsl -e bash -lc "bash $pipeWsl/comfy_start.sh && python3 $pipeWsl/01_generate.py $projWsl $($Ids -join ' '); bash $pipeWsl/comfy_stop.sh"
        wsl -e bash -lc "$conda; conda activate trellis2; python $pipeWsl/02_select_matte.py $projWsl sheet $($Ids -join ' ')"
        Write-Host 'review work\props\*\sheet.png and work\decals\*\sheet.png, write work\selection.json, then run: models'
    }
    'models' {
        wsl -e bash -lc "$conda; conda activate trellis2; python $pipeWsl/02_select_matte.py $projWsl matte $($Ids -join ' ')"
        wsl -e bash -lc "bash $pipeWsl/trellis_loop.sh $projWsl 8"
    }
    'materials' { Run python @((Join-Path $pipe '10_materials.py'), 'build', $Project) }
    'decals' { Run python (@((Join-Path $pipe '11_decals.py'), $Project) + $Ids) }
    'layout' { Run python @((Join-Path $pipe '30_layout.py'), $Project) }
    'kit' {
        Run python @((Join-Path $pipe '30_layout.py'), $Project)
        Blender '21_build_kit.py' @()
    }
    'props' {
        $extra = @($Ids)
        if ($Retexture) { $extra += '--retexture' }
        Blender '20_bake_props.py' $extra
        Write-Host "read $export\props\bake_report.md: look at every check / review row"
    }
    'engine' {
        if ($engine -eq 'unreal') {
            $script = (Join-Path $pipe 'engines\unreal\t2s_unreal.py') -replace '\\', '/'
            $cmd = "py $script --export $($export -replace '\\','/') --exit-when-done"
            if ($Shots) { $cmd += ' --shots' }
            # passed verbatim: PowerShell 5.1 would strip the quotes Unreal needs around the -ExecCmds value
            $argLine = "`"$($cfg.unreal_project)`" -unattended -nosplash -ExecCmds=`"$cmd`""
            Write-Host "> $($cfg.unreal_editor) $argLine" -ForegroundColor DarkGray
            Start-Process -FilePath $cfg.unreal_editor -ArgumentList $argLine -Wait
            Get-Content (Join-Path $export 'unreal_import.log')
        }
        else {
            $dst = Join-Path $cfg.unity_project 'Assets\T2S\Editor'
            New-Item -ItemType Directory -Force $dst | Out-Null
            Copy-Item (Join-Path $pipe 'engines\unity\Editor\T2SImporter.cs') $dst -Force
            $log = Join-Path $export 'unity_import.log'
            $argList = @('-batchmode', '-projectPath', $cfg.unity_project, '-executeMethod', 'T2S.T2SImporter.CommandLine',
                         '-t2sExport', $export, '-quit', '-logFile', $log)
            if ($Shots) { $argList += '-t2sShots' }
            Run $cfg.unity_editor $argList
            Select-String -Path $log -Pattern '\[t2s\]|error CS|Exception' | ForEach-Object { $_.Line }
        }
    }
    'build' {
        Run python @((Join-Path $pipe '12_shared.py'), $Project)
        Run python @((Join-Path $pipe '10_materials.py'), 'build', $Project)
        Run python @((Join-Path $pipe '11_decals.py'), $Project)
        Run python @((Join-Path $pipe '30_layout.py'), $Project)
        Blender '21_build_kit.py' @()
        Blender '20_bake_props.py' @()
        Run python @((Join-Path $pipe '30_layout.py'), $Project)      # again, with real triangle counts
        & $PSCommandPath engine -Project $Project -Shots:$Shots
    }
}
