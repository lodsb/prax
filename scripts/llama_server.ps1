<#
.SYNOPSIS
Start llama.cpp's llama-server as the OpenAI-compatible backend prax.yaml
names as an `openai` model (docs/howto.md 3k).

.EXAMPLE
  scripts\llama_server.ps1 -Model C:\models\Qwen2.5-32B-Instruct-Q4_K_M.gguf
  scripts\llama_server.ps1 -Model <gguf> -Slots 4 -CtxPerSlot 8192 -Port 8080
  scripts\llama_server.ps1 -Model Qwen3.6-27B-Q4_K_M.gguf -Slots 4 -NoThinking

Every slot gets CtxPerSlot tokens of context (the server splits -c evenly),
and the KV cache is stored at 8 bits so a 32B model at Q4 and two slots
of 8 K fit a 24 GB card. --load-mode none: with the file memory-mapped, Windows
keeps the 20 GB resident in host RAM next to the VRAM copy and starves
the door; without it the host copy is freed after the upload. Extraction prompts are about 3,500 tokens in and
1,100 out, so 8 K per slot is the floor. -PowerLimit sets the card's
power cap first (needs an administrator shell; 320 W keeps a 4090 well
within a mid-size power supply).
#>
param(
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Port = 8080,
    [int]$Slots = 2,
    [int]$CtxPerSlot = 8192,
    [string]$Bin = "$env:LOCALAPPDATA\prax\llama.cpp\llama-server.exe",
    [int]$PowerLimit = 0,
    [string]$Alias = "local-server",
    [switch]$NoThinking   # Qwen3.x and Gemma 4 think by default; extraction under a grammar must not
)

if (-not (Test-Path $Bin)) { throw "llama-server not found at $Bin (docs/howto.md 3k)" }
if (-not (Test-Path $Model)) { throw "model not found: $Model" }
if ($PowerLimit -gt 0) { nvidia-smi -pl $PowerLimit | Out-Null }

$ctx = $Slots * $CtxPerSlot
$extra = @()
if ($NoThinking) { $extra += @("--reasoning", "off", "--reasoning-budget", "0") }
& $Bin @extra `
    --model $Model `
    --alias $Alias `
    --host 127.0.0.1 --port $Port `
    --n-gpu-layers 999 --load-mode none `
    --ctx-size $ctx --parallel $Slots `
    --flash-attn on `
    --cache-type-k q8_0 --cache-type-v q8_0 `
    --batch-size 2048 --ubatch-size 512 `
    --threads 8 `
    --no-webui
