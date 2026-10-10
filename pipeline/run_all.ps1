# Text -> 3D object pipeline (FLUX.2 klein 4B object images + TRELLIS.2). The Blender scene itself is not
# scripted: an agent assembles it through the Blender MCP, see AGENT.md step 5.
# scene.json (objects and their prompts) is the single source of truth.
#
#   .\pipeline\run_all.ps1 generate   # step 1 + contact sheets, then STOP for review
#   ... look at work\objects\<id>\sheet.png, write work\selection.json  {"<id>": {"pick": 2, "why": "..."}}
#   .\pipeline\run_all.ps1 models     # steps 2-3: matte, TRELLIS
#
# Finished candidates / GLBs are skipped. Delete the file to redo one object.
param([Parameter(Mandatory)][ValidateSet('generate','models')][string]$Stage)
$ErrorActionPreference = 'Stop'
$proj    = Split-Path -Parent $PSScriptRoot
$projWsl = '/mnt/' + $proj.Substring(0,1).ToLower() + ($proj.Substring(2) -replace '\\','/')
$conda   = 'source ~/miniconda3/etc/profile.d/conda.sh'

if ($Stage -eq 'generate') {
    Write-Host '== 1. FLUX.2 klein 4B candidates (starts ComfyUI, stops it afterwards to free VRAM) =='
    wsl -e bash -lc "bash $projWsl/pipeline/comfy_start.sh && python3 $projWsl/pipeline/01_generate.py $projWsl; bash $projWsl/pipeline/comfy_stop.sh"
    wsl -e bash -lc "$conda; conda activate trellis2; python $projWsl/pipeline/02_select_matte.py $projWsl sheet"
    Write-Host "review work\objects\*\sheet.png and write work\selection.json, then run: .\pipeline\run_all.ps1 models"
} else {
    Write-Host '== 2. background removal of the selected candidates =='
    wsl -e bash -lc "$conda; conda activate trellis2; python $projWsl/pipeline/02_select_matte.py $projWsl matte"
    Write-Host '== 3. TRELLIS.2 image -> 3D =='
    wsl -e bash -lc "bash $projWsl/pipeline/trellis_loop.sh $projWsl 8"
    Write-Host "models are in $proj\work\objects\<id>\model.glb; assemble the scene through the Blender MCP (AGENT.md step 5)"
}
