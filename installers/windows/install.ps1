<#
.SYNOPSIS
  Installs OpenMuscle for the current Windows user. No administrator rights needed.

.DESCRIPTION
  Normally started by double-clicking Install-OpenMuscle.bat. What it does:

    1. Finds Python 3.10 or newer (through the "py" launcher, never the bare
       "python" name, which on a fresh Windows 11 is a Microsoft Store shortcut).
       If there is none it tries "winget" (user scope), and if that is not
       possible it opens the python.org download page and stops with one
       instruction.
    2. Makes a private Python environment in  <data folder>\app  and installs
       OpenMuscle into it (from this download when the script sits inside it,
       otherwise from GitHub).
    3. Creates the data folder (default  %USERPROFILE%\OpenMuscle ) with
       captures, sessions, models, exports and logs inside.
    4. Downloads the offline browser files when online (skipped when offline).
    5. Creates the "OpenMuscle" shortcut on the Desktop and in the Start menu.
    6. Optionally adds the Windows firewall rule the bands need (asks first).
    7. Runs  openmuscle doctor  and says what it found.

  Safe to run again: a second run repairs or upgrades the same install. Close
  the OpenMuscle window first; the installer stops with a message if it is open.
  Everything it does is written to  <data folder>\logs\install.log .

  The data folder can be moved by setting OPENMUSCLE_HOME before running.

.PARAMETER SkipAssets
  Do not download the offline browser files.
.PARAMETER SkipDoctor
  Do not run the final computer check.
.PARAMETER NoPythonInstall
  If Python 3.10+ is missing, only explain how to get it (do not run winget).
.PARAMETER Firewall
  'ask' (default), 'yes' or 'no': whether to add the firewall rule.
.PARAMETER DesktopDir
  Put the Desktop shortcut here instead of the real Desktop (used by tests).
.PARAMETER StartMenuDir
  Put the Start-menu shortcut here instead of the real Programs folder.
.PARAMETER Source
  Folder holding pyproject.toml (the "pc" folder), or any pip requirement.
  Default: the pc folder of the download this script is in, else GitHub.
#>
[CmdletBinding()]
param(
    [switch]$SkipAssets,
    [switch]$SkipDoctor,
    [switch]$NoPythonInstall,
    [ValidateSet('ask', 'yes', 'no')][string]$Firewall = 'ask',
    [string]$DesktopDir,
    [string]$StartMenuDir,
    [string]$Source
)

# Native programs (python, pip, winget) write progress to stderr; that must not
# turn into a script error on Windows PowerShell 5.1.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$RepoUrl = 'https://github.com/Open-Muscle/OpenMuscle-Software'
$GitHubZip = "$RepoUrl/archive/refs/heads/main.zip#subdirectory=pc"
$RawBase = 'https://raw.githubusercontent.com/Open-Muscle/OpenMuscle-Software/main/installers/windows'
$MinMajor = 3
$MinMinor = 10

# ---------------------------------------------------------------- folders --
if ($env:OPENMUSCLE_HOME -and $env:OPENMUSCLE_HOME.Trim()) {
    $Root = [System.IO.Path]::GetFullPath($env:OPENMUSCLE_HOME.Trim())
} else {
    $Root = Join-Path $env:USERPROFILE 'OpenMuscle'
}
$AppDir = Join-Path $Root 'app'
$LogDir = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir 'install.log'
$VenvPython = Join-Path $AppDir 'Scripts\python.exe'
$VenvExe = Join-Path $AppDir 'Scripts\openmuscle.exe'
$Here = $PSScriptRoot
if (-not $Here) { $Here = Split-Path -Parent $MyInvocation.MyCommand.Path }

try {
    New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null
} catch {
    Write-Host ''
    Write-Host "OpenMuscle could not create its folder: $Root" -ForegroundColor Red
    Write-Host $_.Exception.Message
    Write-Host 'Choose a folder you can write to by setting OPENMUSCLE_HOME, then run this again.'
    exit 1
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Add-LogText([string]$text) {
    try { [System.IO.File]::AppendAllText($LogFile, $text, $Utf8NoBom) } catch { }
}

function Write-Log([string]$text) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    Add-LogText "[$stamp] $text`r`n"
}

function Say([string]$text, [string]$color = '') {
    if ($color) { Write-Host $text -ForegroundColor $color } else { Write-Host $text }
    Write-Log $text
}

function Stop-Install([string]$headline, [string[]]$lines) {
    Say ''
    Say "Installation stopped: $headline" 'Red'
    foreach ($l in $lines) { Say "  $l" }
    Say ''
    Say "A detailed log is saved here:  $LogFile"
    exit 1
}

Write-Log '---------------------------------------------------------------'
Write-Log "OpenMuscle install started. PowerShell $($PSVersionTable.PSVersion), Windows $([Environment]::OSVersion.Version)"
Write-Log "Data folder: $Root"
Write-Log "Script folder: $Here"

Say ''
Say 'Installing OpenMuscle' 'Cyan'
Say "Your recordings and the program will live in:  $Root"
Say ''

# -------------------------------------------------------------- find Python --
# Returns @{Path=..; Version=..} when the interpreter is 3.10 or newer, else $null.
function Test-PythonExe([string]$exe, [string[]]$extra = @()) {
    try {
        $out = & $exe @extra -c "import sys; print(sys.version_info[0], sys.version_info[1], sys.version_info[2]); print(sys.executable)" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $lines = @($out)
        $v = $lines[0].Trim().Split(' ')
        $major = [int]$v[0]; $minor = [int]$v[1]
        if ($major -gt $MinMajor -or ($major -eq $MinMajor -and $minor -ge $MinMinor)) {
            return @{ Path = $lines[1].Trim(); Version = "$($v[0]).$($v[1]).$($v[2])" }
        }
        Write-Log "Found Python $($v -join '.') at $exe but it is older than $MinMajor.$MinMinor"
        return $null
    } catch {
        return $null
    }
}

function Find-Python {
    # Preferred order: the versions every needed package ships ready-made files for.
    $minors = @('3.12', '3.13', '3.11', '3.10', '3.14')
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($m in $minors) {
            $r = Test-PythonExe $py.Source @("-$m")
            if ($r) { return $r }
        }
        $r = Test-PythonExe $py.Source @('-3')
        if ($r) { return $r }
    }
    # Python installed without the launcher: look where its installers put it.
    $roots = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    foreach ($m in $minors) {
        $folder = 'Python' + $m.Replace('.', '')
        foreach ($rt in $roots) {
            $exe = Join-Path (Join-Path $rt $folder) 'python.exe'
            if (Test-Path -LiteralPath $exe) {
                $r = Test-PythonExe $exe
                if ($r) { return $r }
            }
        }
    }
    return $null
}

function ConvertTo-ArgText([string]$a) {
    if ($a -match '[\s"]') { return '"' + $a.Replace('"', '\"') + '"' }
    return $a
}

# Runs a program, shows a dot every few seconds, logs all of its output.
# Returns the exit code (-1 when the program could not start). The combined
# output is left in $script:LastOutput.
function Invoke-Logged([string]$exe, [string[]]$arguments, [string]$what) {
    $argText = ($arguments | ForEach-Object { ConvertTo-ArgText $_ }) -join ' '
    Write-Log "RUN ($what): $exe $argText"
    $script:LastOutput = ''
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $exe -ArgumentList $argText -NoNewWindow -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Write-Log "Could not start: $($_.Exception.Message)"
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        return -1
    }
    # Windows PowerShell 5.1 loses the exit code unless the process handle is read.
    $null = $p.Handle
    Write-Host '  ' -NoNewline
    while (-not $p.HasExited) {
        Start-Sleep -Milliseconds 2500
        Write-Host '.' -NoNewline
    }
    Write-Host ''
    $p.WaitForExit()
    $code = $p.ExitCode
    $combined = ''
    foreach ($f in @($outFile, $errFile)) {
        try {
            $txt = Get-Content -LiteralPath $f -Raw -ErrorAction Stop
            if ($txt) {
                $combined += $txt
                Add-LogText $txt
            }
        } catch { }
    }
    $script:LastOutput = $combined
    Write-Log "EXIT $code ($what)"
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    return $code
}

Say '1/6  Looking for Python 3.10 or newer...'
$python = Find-Python
if (-not $python) {
    $canWinget = (-not $NoPythonInstall) -and (Get-Command winget.exe -ErrorAction SilentlyContinue)
    if ($canWinget) {
        Say '     Python was not found. Installing Python 3.12 for your user with winget'
        Say '     (no administrator rights needed; this can take a few minutes).'
        $code = Invoke-Logged 'winget.exe' @('install', '-e', '--id', 'Python.Python.3.12', '--scope', 'user',
            '--silent', '--accept-package-agreements', '--accept-source-agreements') 'winget python'
        if ($code -ne 0) { Say "     winget could not install Python (code $code)." 'Yellow' }
        $python = Find-Python
    }
}
if (-not $python) {
    try { Start-Process 'https://www.python.org/downloads/windows/' } catch { }
    Stop-Install 'Python 3.10 or newer is needed first.' @(
        'I opened the Python download page in your browser (python.org/downloads/windows).',
        '1. Download and run the "Windows installer (64-bit)".',
        '2. On its first screen, TICK the box "Add python.exe to PATH", then click Install Now.',
        '3. When it finishes, double-click Install-OpenMuscle.bat again.')
}
Say "     Using Python $($python.Version)"
Write-Log "Python: $($python.Path)"

# ------------------------------------------------------ running copy check --
# pip cannot replace files that a running OpenMuscle has open (WinError 32) and
# can leave the package half removed, so refuse to start while one is open.
function Test-OpenMuscleRunning {
    if (-not (Test-Path -LiteralPath $AppDir)) { return $false }
    $prefix = ([System.IO.Path]::GetFullPath($AppDir)).TrimEnd('\') + '\'
    try {
        $procs = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    } catch {
        Write-Log "Could not list processes: $($_.Exception.Message)"
        return $false
    }
    foreach ($p in $procs) {
        if ($p.ProcessId -eq $PID) { continue }
        $path = $p.ExecutablePath
        if ($path -and $path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Log "Running copy: $path (pid $($p.ProcessId))"
            return $true
        }
    }
    return $false
}
function Stop-ForRunningCopy {
    Stop-Install 'OpenMuscle is open.' @(
        'Close the OpenMuscle window (the black one), then run this installer again.',
        'Installing while it is open would damage the install.')
}
if (Test-OpenMuscleRunning) { Stop-ForRunningCopy }

# ------------------------------------------------------ virtual environment --
Say '2/6  Setting up the private OpenMuscle environment...'
$venvOk = $false
if (Test-Path -LiteralPath $VenvPython) {
    $r = Test-PythonExe $VenvPython
    if ($r) { $venvOk = $true; Write-Log "Reusing environment ($($r.Version))" }
}
if (-not $venvOk) {
    if (Test-Path -LiteralPath (Join-Path $AppDir 'pyvenv.cfg')) {
        Say '     The old environment is broken; rebuilding it.'
        try {
            Remove-Item -LiteralPath (Join-Path $AppDir 'Scripts'), (Join-Path $AppDir 'Lib'), `
                (Join-Path $AppDir 'Include'), (Join-Path $AppDir 'pyvenv.cfg') -Recurse -Force -ErrorAction Stop
        } catch {
            Stop-Install 'the old environment is in use.' @(
                'Close any OpenMuscle window that is open, then run this installer again.',
                $_.Exception.Message)
        }
    }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    $code = Invoke-Logged $python.Path @('-m', 'venv', $AppDir) 'create environment'
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        Stop-Install 'could not create the Python environment.' @(
            'If you installed Python from the Microsoft Store, install the one from python.org instead',
            '(tick "Add python.exe to PATH"), then run this installer again.')
    }
}

# --------------------------------------------------------------- install it --
Say '3/6  Installing OpenMuscle and its libraries (a few minutes the first time)...'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONUTF8 = '1'
$pc = $null
if ($Source) {
    $pc = $Source
} else {
    $candidate = Join-Path (Split-Path -Parent (Split-Path -Parent $Here)) 'pc'
    if (Test-Path -LiteralPath (Join-Path $candidate 'pyproject.toml')) { $pc = $candidate }
}
if ($pc) { Write-Log "Installing from: $pc" } else { $pc = $GitHubZip; Write-Log "Installing from GitHub: $pc" }

$null = Invoke-Logged $VenvPython @('-m', 'pip', 'install', '--upgrade', 'pip') 'upgrade pip'
$code = Invoke-Logged $VenvPython @('-m', 'pip', 'install', '--upgrade', $pc) 'install openmuscle'
if ($code -eq 0) {
    # A re-run with the same version number would keep the old files; this makes
    # "run the installer again" always put the current code in place.
    $code = Invoke-Logged $VenvPython @('-m', 'pip', 'install', '--no-deps', '--force-reinstall', $pc) 'refresh openmuscle'
}
if ($code -ne 0 -or -not (Test-Path -LiteralPath $VenvExe)) {
    if ($script:LastOutput -match 'WinError 32|being used by another process|Access is denied') {
        Stop-ForRunningCopy
    }
    $net = $script:LastOutput -match 'Failed to establish|Max retries|getaddrinfo|Temporary failure|Network is unreachable|ProxyError|SSLError|No matching distribution|Could not find a version|timed out|ConnectionError'
    if ($net) {
        Stop-Install 'the libraries could not be downloaded.' @(
            'This computer seems to be offline, or a firewall or proxy blocked the download.',
            'Connect to the internet (any Wi-Fi is fine), then double-click Install-OpenMuscle.bat again.')
    }
    Stop-Install 'installing OpenMuscle failed.' @(
        'Run Install-OpenMuscle.bat again. If it fails again, send the log file named below to the person who gave you OpenMuscle.')
}

# ------------------------------------------------------------ data folders --
$env:OPENMUSCLE_HOME = $Root
$null = Invoke-Logged $VenvPython @('-c', 'from openmuscle import paths; paths.ensure_data_dirs()') 'create data folders'

# --------------------------------------------- launcher + icon inside app\ --
function Get-SupportFile([string]$name) {
    $dest = Join-Path $AppDir $name
    $local = Join-Path $Here $name
    if (Test-Path -LiteralPath $local) {
        if ((Resolve-Path -LiteralPath $local).Path -ne $dest) {
            Copy-Item -LiteralPath $local -Destination $dest -Force
        }
    } else {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri "$RawBase/$name" -OutFile $dest -ErrorAction Stop
        } catch {
            Write-Log "Could not download $name : $($_.Exception.Message)"
            return $false
        }
    }
    try { Unblock-File -LiteralPath $dest -ErrorAction SilentlyContinue } catch { }
    return (Test-Path -LiteralPath $dest)
}
$launcher = Join-Path $AppDir 'OpenMuscle.bat'
$icon = Join-Path $AppDir 'openmuscle.ico'
if (-not (Get-SupportFile 'OpenMuscle.bat')) {
    Stop-Install 'the launcher file could not be copied.' @(
        'Extract the whole downloaded ZIP (not just one file) and run Install-OpenMuscle.bat from inside it.')
}
$iconOk = Get-SupportFile 'openmuscle.ico'

# ------------------------------------------------------- offline assets -----
if ($SkipAssets) {
    Say '4/6  Skipping the offline browser files (as requested).'
} else {
    Say '4/6  Downloading the offline camera and 3D viewer files...'
    $code = Invoke-Logged $VenvExe @('assets', 'fetch') 'assets fetch'
    if ($code -ne 0) {
        Say '     Could not download them (no internet?). OpenMuscle still works; the camera page' 'Yellow'
        Say '     will load them from the internet when you use it. To try again later, run this installer again.' 'Yellow'
    }
}

# ------------------------------------------------------------- shortcuts ----
Say '5/6  Creating the OpenMuscle shortcut...'
function New-Shortcut([string]$folder) {
    try {
        New-Item -ItemType Directory -Force -Path $folder -ErrorAction Stop | Out-Null
        $lnk = Join-Path $folder 'OpenMuscle.lnk'
        $sh = New-Object -ComObject WScript.Shell
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = $launcher
        $s.WorkingDirectory = $Root
        $s.Description = 'Start OpenMuscle (leave the window open while you use it)'
        $s.WindowStyle = 1
        if ($iconOk) { $s.IconLocation = "$icon,0" }
        $s.Save()
        Write-Log "Shortcut created: $lnk"
        return $lnk
    } catch {
        Write-Log "Shortcut failed in $folder : $($_.Exception.Message)"
        return $null
    }
}
if (-not $DesktopDir) { $DesktopDir = [Environment]::GetFolderPath('Desktop') }
if (-not $StartMenuDir) { $StartMenuDir = [Environment]::GetFolderPath('Programs') }
$deskLink = New-Shortcut $DesktopDir
$menuLink = New-Shortcut $StartMenuDir
if ($deskLink) { Say "     Desktop shortcut:     $deskLink" }
if ($menuLink) { Say "     Start-menu shortcut:  $menuLink" }
if (-not $deskLink -and -not $menuLink) {
    Say '     Could not create a shortcut. You can still start OpenMuscle by double-clicking:' 'Yellow'
    Say "     $launcher" 'Yellow'
}

# --------------------------------------------------------------- firewall ---
function Test-FirewallRule {
    $out = & netsh.exe advfirewall firewall show rule name="OpenMuscle UDP 3140-3141" 2>$null
    return ($LASTEXITCODE -eq 0 -and ($out -join "`n") -match 'OpenMuscle')
}
Say '6/6  Checking the Windows firewall and your computer...'
if (Test-FirewallRule) {
    Say '     The OpenMuscle firewall rule is already in place.'
} else {
    $answer = $Firewall
    if ($answer -eq 'ask') {
        Say ''
        Say '     The bands send their data to this computer over Wi-Fi, and Windows blocks that'
        Say '     until it is allowed. I can add one rule for home and work networks (not public'
        Say '     ones) that allows incoming UDP ports 3140 and 3141 for OpenMuscle.'
        Say '     Windows will show a normal "Do you want to allow this app to make changes"'
        Say '     question. Answer Yes there. You can skip this and add the rule later.'
        $reply = ''
        try { $reply = Read-Host '     Add the firewall rule now? (Y/n)' } catch { $reply = 'n' }
        if ($reply -match '^\s*(n|no)\s*$') { $answer = 'no' } else { $answer = 'yes' }
    }
    if ($answer -eq 'yes') {
        $code = Invoke-Logged $VenvExe @('firewall', '--yes') 'firewall rule'
        if ($code -eq 0) {
            Say '     Firewall rule added.'
        } else {
            Say '     The firewall rule was not added (the question was declined or failed).' 'Yellow'
            Say '     You can add it later by closing OpenMuscle and running this installer again.' 'Yellow'
        }
    } else {
        Say '     Skipped the firewall rule. If the bands do not appear later, close OpenMuscle and run this installer again.'
    }
}

if ($SkipDoctor) {
    Write-Log 'Doctor skipped (-SkipDoctor).'
} else {
    $code = Invoke-Logged $VenvExe @('doctor', '--no-discover') 'doctor'
    if ($code -eq 0) {
        Say '     Computer check: no problems found.'
    } else {
        Say '     The computer check found something to look at. It does not stop the install;' 'Yellow'
        Say '     the OpenMuscle window shows the same advice. Details are in the log.' 'Yellow'
    }
}

# ----------------------------------------------------------------- finish ---
Write-Log 'Install finished OK.'
Say ''
Say 'OpenMuscle is installed.' 'Green'
Say ''
Say '  To start it:  double-click the OpenMuscle icon on your Desktop'
Say '                (or find OpenMuscle in the Start menu).'
Say '  A black window opens and your web browser shows OpenMuscle.'
Say '  Leave the black window open while you work. Closing it stops OpenMuscle.'
Say ''
Say "  Your recordings are saved in:  $(Join-Path $Root 'captures')"
Say ''
exit 0
