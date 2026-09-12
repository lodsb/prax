<#
.SYNOPSIS
Start or stop the prax worker on this Windows machine as a detached
process: the machine with the models drains a door's queue (parse, titles,
extract, embed) and uploads Downloads\prax-inbox.

.EXAMPLE
  deploy\worker.ps1                                  # the door on this machine
  deploy\worker.ps1 -Door http://board:8000 -Token <token>
  deploy\worker.ps1 -Stop

Logs go to <data dir>\logs\worker.*.log. The console script prax.exe
launches a child python.exe; -Stop ends both, which Stop-Process on the
.exe alone does not (three workers piled up that way once).
#>
param(
    [string]$Door = $env:PRAX_DOOR,
    [string]$Token = $env:PRAX_TOKEN,
    [string]$DataDir = $env:PRAX_DATA_DIR,
    [int]$Interval = 20,
    [switch]$Stop
)

$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$prax = Join-Path $repo '.venv\Scripts\prax.exe'
if (-not $Door) { $Door = 'http://127.0.0.1:8000' }
if (-not $DataDir) { $DataDir = Join-Path $repo 'data' }

$running = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'prax\.exe.* work ' }
if ($Stop) {
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    "stopped $(@($running).Count) process(es)"
    return
}
if ($running) {
    "a worker is already running (pid $($running[0].ProcessId)); -Stop first"
    return
}

$logs = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$env:PRAX_DATA_DIR = $DataDir
$env:PRAX_DOOR = $Door
if ($Token) { $env:PRAX_TOKEN = $Token }
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
Start-Process -FilePath $prax `
    -ArgumentList 'work', '--watch', '--interval', "$Interval" `
    -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'worker.out.log') `
    -RedirectStandardError (Join-Path $logs 'worker.err.log')
"worker started for $Door; logs in $logs"
