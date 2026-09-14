<#
.SYNOPSIS
The desktop as prax's server: the door, llama-server and the worker as
tasks that start when you log on and come back after a crash, a nightly
backlog pass, a nightly backup. Task Scheduler under your own account —
no service, no password stored, nothing system-wide.

.DESCRIPTION
-Install registers five tasks in the \prax\ folder of Task Scheduler:

  prax llama-server   at logon   scripts\llama_server.ps1 with -LlamaModel and -LlamaArgs
  prax door           at logon   prax serve --host <BindHost> --port <Port>
  prax worker         at logon   prax work --watch (the model work, through the door)
  prax nightly        03:00      prax work --scope all --limit <NightlyLimit>, then prax maintain (howto 3l¾)
  prax backup         04:30      prax backup <Backup> [--no-archive]   (only with -Backup)

Each task runs this script again with -Run <name>, which sets the
environment, rotates the logs (<DataDir>\logs\<name>.log and .err.log,
ten rotations kept; <name>.runs.log lists every start and exit) and
runs the process in the foreground, so the task shows Running while it
lives and restarts it when it dies. The tasks
run while you are logged on (a locked screen is fine; logged off is
not), which is what "no password stored" costs.

Secrets are never written into a task: the door's token comes from the
PRAX_TOKEN user environment variable, or from <DataDir>\door.token
(one line); without either the door answers loopback only. The
Anthropic key comes from the ANTHROPIC_API_KEY user variable, as the
worker always did. The card's power cap (nvidia-smi -pl) needs an
administrator and stays your own task.

.EXAMPLE
  deploy\desktop.ps1 -Install -DataDir C:\prax-data -Backup I:\prax-backup `
      -LlamaModel C:\models\Qwen3.6-35B-A3B-UD-Q4_K_S.gguf `
      -LlamaArgs "-Mmproj mmproj-F16.gguf -Slots 2 -CpuMoe 2 -UBatch 256 -ImageMaxTokens 1024 -NoThinking"
  deploy\desktop.ps1 -Start              # now, without waiting for a logon
  deploy\desktop.ps1 -Status
  deploy\desktop.ps1 -Stop -Only worker
  deploy\desktop.ps1 -Uninstall          # the tasks; the store is untouched
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$Start,
    [switch]$Stop,
    [ValidateSet("door", "llama-server", "worker", "nightly", "backup")]
    [string]$Run = "",
    [ValidateSet("", "door", "llama-server", "worker", "nightly", "backup")]
    [string]$Only = "",
    [string]$DataDir = $env:PRAX_DATA_DIR,
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8000,
    [string]$LlamaModel = "",
    [string]$LlamaArgs = "",
    [string]$Backup = "",
    [switch]$BackupArchive,
    [int]$NightlyLimit = 100,
    [string]$NightlySteps = "parse,titles,extract,embed",
    [int]$Interval = 20
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$self = $MyInvocation.MyCommand.Path
$prax = Join-Path $repo ".venv\Scripts\prax.exe"
$TaskPath = "\prax\"
$Names = @("llama-server", "door", "worker", "nightly", "backup")
if (-not $DataDir) { $DataDir = Join-Path $repo "data" }
$DataDir = [System.IO.Path]::GetFullPath($DataDir)

function Quote([string]$s) { return '"' + $s.Replace('"', '\"') + '"' }

# ------------------------------------------------------------ environment

function Set-PraxEnvironment {
    $env:PRAX_DATA_DIR = $DataDir
    if (-not $env:PRAX_TOKEN) {
        $env:PRAX_TOKEN = [Environment]::GetEnvironmentVariable("PRAX_TOKEN", "User")
    }
    $file = Join-Path $DataDir "door.token"
    if (-not $env:PRAX_TOKEN -and (Test-Path $file)) {
        $env:PRAX_TOKEN = (Get-Content $file -TotalCount 1).Trim()
    }
    if (-not $env:ANTHROPIC_API_KEY) {
        $env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
    }
    $env:PRAX_DOOR = "http://127.0.0.1:$Port"
}

# The logs: <DataDir>\logs\<name>.log (stdout), <name>.err.log (stderr;
# uvicorn and llama-server write there), <name>.runs.log (every start and
# exit with its code). A start rotates the first two; ten rotations kept.
function Rotate-Logs([string]$name) {
    $logs = Join-Path $DataDir "logs"
    New-Item -ItemType Directory -Force $logs | Out-Null
    foreach ($suffix in @("log", "err.log")) {
        $f = Join-Path $logs "$name.$suffix"
        if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) {
            $stamp = (Get-Item $f).LastWriteTime.ToString("yyyyMMdd-HHmmss")
            Move-Item $f (Join-Path $logs "$name.$stamp.$suffix") -Force
        }
    }
    Get-ChildItem $logs -Filter "$name.20*" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 10 | Remove-Item -Force -ErrorAction SilentlyContinue
    return @{
        out  = Join-Path $logs "$name.log"
        err  = Join-Path $logs "$name.err.log"
        runs = Join-Path $logs "$name.runs.log"
    }
}

# Run one process in the foreground with its output in the logs; the
# exit code is the task's, so Task Scheduler restarts a crash. Several
# commands (the nightly: the worker's pass, then the maintenance pass)
# run one after the other in the same logs; the worst exit code is the task's.
function Invoke-Logged([string]$name, [string]$exe, [string[]]$arguments, [string[][]]$then = @()) {
    $l = Rotate-Logs $name
    $worst = 0
    foreach ($command in @(, $arguments) + $then) {
        $quoted = $command | ForEach-Object { if ($_ -match "\s") { '"' + $_ + '"' } else { $_ } }
        "$(Get-Date -Format s) start: $exe $($quoted -join ' ')" | Add-Content $l.runs -Encoding utf8
        $p = Start-Process -FilePath $exe -ArgumentList $quoted -WorkingDirectory $repo `
            -NoNewWindow -PassThru -Wait `
            -RedirectStandardOutput "$($l.out).part" -RedirectStandardError "$($l.err).part"
        Get-Content "$($l.out).part" -ErrorAction SilentlyContinue | Add-Content $l.out -Encoding utf8
        Get-Content "$($l.err).part" -ErrorAction SilentlyContinue | Add-Content $l.err -Encoding utf8
        Remove-Item "$($l.out).part", "$($l.err).part" -ErrorAction SilentlyContinue
        "$(Get-Date -Format s) exit $($p.ExitCode)" | Add-Content $l.runs -Encoding utf8
        if ($p.ExitCode -ne 0) { $worst = $p.ExitCode }
    }
    exit $worst
}

# ------------------------------------------------------------------ -Run

if ($Run) {
    Set-PraxEnvironment
    switch ($Run) {
        "door" {
            Invoke-Logged "door" $prax @("serve", "--host", $BindHost, "--port", "$Port")
        }
        "worker" {
            Invoke-Logged "worker" $prax @("work", "--watch", "--interval", "$Interval")
        }
        "nightly" {
            # the worker's backlog pass, then what the store does to itself
            Invoke-Logged "nightly" $prax @("work", "--scope", "all", "--limit", "$NightlyLimit", "--steps", $NightlySteps) @(, @("maintain"))
        }
        "backup" {
            if (-not $Backup) { throw "no backup directory: -Install with -Backup" }
            $arguments = @("backup", $Backup)
            if (-not $BackupArchive) { $arguments += "--no-archive" }
            Invoke-Logged "backup" $prax $arguments
        }
        "llama-server" {
            if (-not $LlamaModel) { throw "no model: -Install with -LlamaModel" }
            $script = Join-Path $repo "scripts\llama_server.ps1"
            $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
            $arguments = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $script, "-Model", $LlamaModel)
            if ($LlamaArgs) { $arguments += ($LlamaArgs -split "\s+" | Where-Object { $_ }) }
            Invoke-Logged "llama-server" $ps $arguments
        }
    }
    return
}

# --------------------------------------------------------------- helpers

function Get-PraxTask([string]$name) {
    return Get-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name" -ErrorAction SilentlyContinue
}

function Chosen() {
    if ($Only) { return @($Only) }
    return $Names
}

function Get-PraxProcesses() {
    return Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq "llama-server.exe") -or
        ($_.CommandLine -match "prax\.exe.? (serve|work)")
    }
}

# --------------------------------------------------------------- -Install

if ($Install) {
    if (-not (Test-Path $prax)) { throw "no prax.exe in $repo\.venv (docs/howto.md 1)" }
    if (-not (Test-Path $DataDir)) { throw "no data directory at $DataDir" }
    if ($LlamaModel -and -not (Test-Path $LlamaModel)) { throw "model not found: $LlamaModel" }
    if ($Backup -and -not [System.IO.Path]::IsPathRooted($Backup)) { throw "-Backup must be an absolute path" }

    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $common = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File $(Quote $self) -DataDir $(Quote $DataDir) -Port $Port"
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $forever = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
    $bounded = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
        -MultipleInstances IgnoreNew -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden

    $tasks = @()
    if ($LlamaModel) {
        $t = New-ScheduledTaskTrigger -AtLogOn -User $user
        $t.Delay = "PT5S"
        $tasks += @{ name = "llama-server"; trigger = $t; settings = $forever
            args = "$common -Run llama-server -LlamaModel $(Quote $LlamaModel) -LlamaArgs $(Quote $LlamaArgs)"
            what = "llama-server at logon: $LlamaModel $LlamaArgs" }
    }
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $t.Delay = "PT15S"
    $tasks += @{ name = "door"; trigger = $t; settings = $forever
        args = "$common -Run door -BindHost $BindHost"
        what = "the door at logon: prax serve --host $BindHost --port $Port" }
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $t.Delay = "PT45S"
    $tasks += @{ name = "worker"; trigger = $t; settings = $forever
        args = "$common -Run worker -Interval $Interval"
        what = "the worker at logon: prax work --watch --interval $Interval" }
    $tasks += @{ name = "nightly"; trigger = (New-ScheduledTaskTrigger -Daily -At 03:00); settings = $bounded
        args = "$common -Run nightly -NightlyLimit $NightlyLimit -NightlySteps $(Quote $NightlySteps)"
        what = "nightly at 03:00: prax work --scope all --limit $NightlyLimit --steps $NightlySteps, then prax maintain" }
    if ($Backup) {
        $bargs = "$common -Run backup -Backup $(Quote $Backup)"
        if ($BackupArchive) { $bargs += " -BackupArchive" }
        $how = "--no-archive (the database, indexes and config)"
        if ($BackupArchive) { $how = "with the archive" }
        $tasks += @{ name = "backup"; trigger = (New-ScheduledTaskTrigger -Daily -At 04:30); settings = $bounded
            args = $bargs
            what = "backup at 04:30: prax backup $Backup $how" }
    }

    foreach ($task in $tasks) {
        $action = New-ScheduledTaskAction -Execute $ps -Argument $task.args -WorkingDirectory $repo
        Register-ScheduledTask -TaskPath $TaskPath -TaskName "prax $($task.name)" `
            -Action $action -Trigger $task.trigger -Principal $principal -Settings $task.settings `
            -Description $task.what -Force | Out-Null
        "registered  prax $($task.name)`t$($task.what)"
    }
    foreach ($name in $Names) {
        if (-not ($tasks | Where-Object { $_.name -eq $name })) {
            $old = Get-PraxTask $name
            if ($old) { Unregister-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name" -Confirm:$false; "removed     prax $name (not in this install)" }
        }
    }
    ""
    "Not started: deploy\desktop.ps1 -Start starts them now; a logon starts them anyway."
    $token = [Environment]::GetEnvironmentVariable("PRAX_TOKEN", "User")
    if (-not $token -and -not (Test-Path (Join-Path $DataDir "door.token"))) {
        "No token yet: the door will answer this machine only. Set the PRAX_TOKEN user"
        "variable or write one line to $(Join-Path $DataDir 'door.token'), then restart the door task."
    }
    return
}

# ------------------------------------------------------------- -Uninstall

if ($Uninstall) {
    foreach ($name in (Chosen)) {
        $t = Get-PraxTask $name
        if ($t) {
            if ($t.State -eq "Running") { Stop-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name" }
            Unregister-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name" -Confirm:$false
            "removed  prax $name"
        }
    }
    return
}

# ---------------------------------------------------------- -Start / -Stop

if ($Stop) {
    foreach ($name in (Chosen)) {
        $t = Get-PraxTask $name
        if ($t -and $t.State -eq "Running") { Stop-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name"; "stopped  prax $name" }
    }
    # what is still running: a task's child that outlived the task (Task
    # Scheduler ends the wrapper, not always its tree) or a process started
    # by hand, either in the way of -Start
    Start-Sleep -Seconds 2
    $stray = Get-PraxProcesses | Where-Object {
        $n = $_.Name; $c = $_.CommandLine
        (-not $Only) -or
        ($Only -eq "llama-server" -and $n -eq "llama-server.exe") -or
        ($Only -eq "door" -and $c -match "prax\.exe.? serve") -or
        ($Only -eq "worker" -and $c -match "prax\.exe.? work --watch")
    }
    foreach ($p in $stray) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        "ended    pid $($p.ProcessId) $($p.Name) (still running)"
    }
    if (-not $Start) { return }
}

if ($Start) {
    foreach ($name in (Chosen)) {
        if ($name -in @("nightly", "backup")) { continue }  # those have their hour
        $t = Get-PraxTask $name
        if (-not $t) { "no task prax $name (-Install first)"; continue }
        if ($t.State -eq "Running") { "running  prax $name"; continue }
        Start-ScheduledTask -TaskPath $TaskPath -TaskName "prax $name"
        "started  prax $name"
        if ($name -eq "door") {
            # the worker announces itself to the door once, at its start
            $up = $false
            foreach ($i in 1..30) {
                Start-Sleep -Seconds 2
                # any answer counts, a 401 included: the door is up, the token is its business
                try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port/changes" -UseBasicParsing -TimeoutSec 3 | Out-Null; $up = $true; break }
                catch { if ($_.Exception.Response) { $up = $true; break } }
            }
            if (-not $up) { "         (the door is not answering yet; see logs\door.err.log)" }
        } else { Start-Sleep -Seconds 3 }
    }
    return
}

# --------------------------------------------------------------- -Status

Set-PraxEnvironment
"tasks (Task Scheduler, \prax\):"
foreach ($name in $Names) {
    $t = Get-PraxTask $name
    if (-not $t) { "  {0,-14} not installed" -f $name; continue }
    $i = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName "prax $name"
    $last = ""
    if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) {
        $result = "exit $($i.LastTaskResult)"
        if ($i.LastTaskResult -eq 0) { $result = "ok" }
        if ($i.LastTaskResult -eq 267009) { $result = "" }  # 0x41301: still running
        $last = "last $($i.LastRunTime.ToString('yyyy-MM-dd HH:mm')) $result"
    }
    $next = ""
    if ($i.NextRunTime -and $i.NextRunTime.Year -gt 2000) { $next = "next $($i.NextRunTime.ToString('yyyy-MM-dd HH:mm'))" }
    "  {0,-14} {1,-9} {2} {3}" -f $name, $t.State, $last, $next
}
"processes:"
$procs = Get-PraxProcesses
if (-not $procs) { "  none" }
$roles = @{}
foreach ($p in $procs) {
    $what = "llama-server"
    if ($p.CommandLine -match "prax\.exe.? serve") { $what = "door" }
    if ($p.CommandLine -match "prax\.exe.? work --watch") { $what = "worker" }
    elseif ($p.CommandLine -match "prax\.exe.? work") { $what = "nightly" }
    if (-not $roles[$what]) { $roles[$what] = @{ pids = @(); since = $p.CreationDate } }
    $roles[$what].pids += $p.ProcessId  # the launcher exe and its python child
}
foreach ($what in $roles.Keys) {
    "  {0,-14} pid {1,-14} since {2}" -f $what, ($roles[$what].pids -join ","), $roles[$what].since.ToString("yyyy-MM-dd HH:mm")
}
try {
    $h = @{}
    if ($env:PRAX_TOKEN) { $h["Authorization"] = "Bearer $env:PRAX_TOKEN" }
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/changes" -Headers $h -UseBasicParsing -TimeoutSec 10
    $j = $r.Content | ConvertFrom-Json
    "door:  answering on :$Port, $($j.jobs) job(s) running"
} catch {
    if ($_.Exception.Response) { "door:  answering on :$Port, but not to this token (HTTP $([int]$_.Exception.Response.StatusCode))" }
    else { "door:  not answering on :$Port ($($_.Exception.Message))" }
}
if (-not $env:PRAX_TOKEN) { "token: none (the door answers this machine only)" } else { "token: set" }
"logs:  $(Join-Path $DataDir 'logs')"
