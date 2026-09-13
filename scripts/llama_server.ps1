<#
.SYNOPSIS
Start llama.cpp's llama-server as the OpenAI-compatible backend prax.yaml
names as an `openai` model (docs/howto.md 3k).

.EXAMPLE
  scripts\llama_server.ps1 -Model C:\models\Qwen2.5-32B-Instruct-Q4_K_M.gguf
  scripts\llama_server.ps1 -Model <gguf> -Slots 4 -CtxPerSlot 8192 -Port 8080
  scripts\llama_server.ps1 -Model Qwen3.6-27B-Q4_K_M.gguf -Slots 4 -NoThinking
  scripts\llama_server.ps1 -Model Qwen3.6-35B-A3B-UD-Q4_K_S.gguf -Mmproj mmproj-F16.gguf -Slots 3 -NoThinking

-Reranker starts a reranker instead (a cross-encoder GGUF such as
bge-reranker-v2-m3, `--reranking`, one slot, its own port, no chat), the
`server` reranker of prax.yaml's `rerank:` section:

  scripts\llama_server.ps1 -Model bge-reranker-v2-m3-Q8_0.gguf -Reranker -Port 8081

-Mmproj loads the model's multimodal projector (the mmproj-*.gguf beside
a vision-language model's weights, about 1 GB) so the same server also
describes images (the `vision` extractor); a name without a directory is
looked for next to the model. -Metrics exposes /metrics (Prometheus text)
for the door's Jobs page; on by default.

VRAM on a card that also drives the display: the 35B-A3B at Q4 with three
8 K slots took 22.1 GB of the 4090's 24.5 (nvidia-smi's total), and the
projector with its compute buffers pushed that to 23.7 — the desktop's
own programs were starved and the display froze (2026-09-14). Measured
by the server's own dedicated memory (Get-Counter '\GPU Process Memory'):
  3 slots + projector, -UBatch 256 -ImageMaxTokens 1024   21.1 GB, 167 tok/s
  2 slots + projector, same                               20.8 GB
  2 slots + projector, same, -CpuMoe 2                    20.1 GB, 148 tok/s
  -CpuMoe 8                                                       23 tok/s
  -ProjectorOnCpu: an image took minutes to encode; do not
The slot count changes little (a q8 8 K slot is ~0.3 GB); -CpuMoe N keeps
the expert weights of the first N layers in RAM (a MoE model's bulk) and
is the dial that matters — 2 costs a tenth of the speed, 8 most of it.
The desktop wants 3-4 GB; the last line below is the configuration in
use.

  scripts\llama_server.ps1 -Model <gguf> -Mmproj mmproj-F16.gguf -Slots 2 -CpuMoe 2 -UBatch 256 -ImageMaxTokens 1024 -NoThinking

Every slot gets CtxPerSlot tokens of context (the server splits -c evenly),
and the KV cache is stored at 8 bits so a 32B model at Q4 and two slots
of 8 K fit a 24 GB card. Memory on Windows (measured 2026-09-12 with a
22 GB model on a 32 GB machine): the driver backs every VRAM allocation
with system commit, so the server charges 20-30 GB of commit whatever
the load mode; --load-mode mmap keeps that at about 22 GB (the model's
pages are file-backed and evictable, so its working set looks large but
is reclaimable), --load-mode none at about 30 GB with a smaller working
set. With IDEs and a browser open the commit limit is what runs out
first (allocation failures in other programs), so a page file of at
least twice the RAM is the setting that matters. Extraction prompts are about 3,500 tokens in and
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
    [string]$Mmproj = "",
    [int]$CpuMoe = 0,          # expert weights of the first N layers stay in RAM (VRAM headroom)
    [switch]$ProjectorOnCpu,   # image encoding on the CPU, the projector's VRAM freed
    [int]$ImageMaxTokens = 0,  # cap on what one image may take (its buffers scale with it)
    [int]$UBatch = 512,        # physical batch; smaller means smaller compute buffers
    [switch]$Reranker,
    [switch]$NoMetrics,
    [switch]$NoThinking   # Qwen3.x and Gemma 4 think by default; extraction under a grammar must not
)

if (-not (Test-Path $Bin)) { throw "llama-server not found at $Bin (docs/howto.md 3k)" }
if (-not (Test-Path $Model)) { throw "model not found: $Model" }
if ($PowerLimit -gt 0) { nvidia-smi -pl $PowerLimit | Out-Null }

$ctx = $Slots * $CtxPerSlot
$extra = @()
if ($NoThinking) { $extra += @("--reasoning", "off", "--reasoning-budget", "0") }
if ($Mmproj) {
    if (-not (Test-Path $Mmproj)) { $Mmproj = Join-Path (Split-Path $Model) $Mmproj }
    if (-not (Test-Path $Mmproj)) { throw "projector not found: $Mmproj" }
    $extra += @("--mmproj", $Mmproj)
    if ($ProjectorOnCpu) { $extra += @("--no-mmproj-offload") }
    if ($ImageMaxTokens -gt 0) { $extra += @("--image-max-tokens", $ImageMaxTokens) }
}
if ($CpuMoe -gt 0) { $extra += @("--n-cpu-moe", $CpuMoe) }
if (-not $NoMetrics) { $extra += @("--metrics") }
if ($Reranker) {
    # a cross-encoder: the query and a candidate in one sequence, read in one
    # batch, one score out; so the batch is the context, and 4096 tokens
    # covers any chunk prax sends
    & $Bin @extra --model $Model --alias $Alias --host 127.0.0.1 --port $Port `
        --reranking --n-gpu-layers 999 --ctx-size 4096 --parallel 1 `
        --batch-size 4096 --ubatch-size 4096 --threads 8 --no-webui
    exit $LASTEXITCODE
}
& $Bin @extra `
    --model $Model `
    --alias $Alias `
    --host 127.0.0.1 --port $Port `
    --n-gpu-layers 999 --load-mode mmap `
    --ctx-size $ctx --parallel $Slots `
    --flash-attn on `
    --cache-type-k q8_0 --cache-type-v q8_0 `
    --batch-size 2048 --ubatch-size $UBatch `
    --threads 8 `
    --no-webui
