param(
    [ValidateSet('Info', 'Exec', 'Deploy', 'Launch', 'Monitor', 'Reboot')]
    [string]$Action = 'Info',
    [string]$CodeFile,
    [string]$Code,
    [int]$Seconds = 5,
    [int]$TimeoutSeconds = 15,
    [string[]]$Only
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$boardSerial = '8792b44d9c11021d'

# Enumerate Windows metadata only. Never open a port to discover its identity.
$projectPorts = @()
foreach ($candidate in @(Get-PnpDevice -PresentOnly -Class Ports)) {
    if ($candidate.FriendlyName -notmatch '\((COM\d+)\)$') { continue }
    $candidatePort = $Matches[1]
    if ($candidatePort -in @('COM4', 'COM5')) { continue }
    $candidateId = $candidate.InstanceId
    for ($depth = 0; $depth -lt 4 -and $candidateId; $depth++) {
        if ($candidateId -ieq "USB\VID_2E8A&PID_0005\$boardSerial") {
            $projectPorts += $candidatePort
            break
        }
        $candidateId = (Get-PnpDeviceProperty -InstanceId $candidateId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
    }
}
if ($projectPorts.Count -ne 1) { throw 'Project Pico USB serial not uniquely present; no serial ports opened.' }
$projectPort = $projectPorts[0]
if ($projectPort -in @('COM4', 'COM5')) { throw 'Protected port' }
Write-Output "Project Pico $boardSerial on $projectPort"
$connection = New-Object System.IO.Ports.SerialPort $projectPort,115200,None,8,one
$connection.DtrEnable = $true
$connection.RtsEnable = $true
$connection.ReadTimeout = 3000
$connection.WriteTimeout = 3000
$leaveRunning = $false

function Send-Code([string]$source) {
    $encoded = [Text.Encoding]::UTF8.GetBytes($source)
    for ($offset = 0; $offset -lt $encoded.Length; $offset += 128) {
        $connection.Write($encoded, $offset, [Math]::Min(128, $encoded.Length - $offset))
        Start-Sleep -Milliseconds 2
    }
    $connection.Write([string][char]4)
}

function Invoke-Raw([string]$source, [int]$timeout = 15) {
    Send-Code $source
    $received = ''
    $deadline = (Get-Date).AddSeconds($timeout)
    do {
        Start-Sleep -Milliseconds 10
        $received += $connection.ReadExisting()
        $markers = @($received.ToCharArray() | Where-Object { [int]$_ -eq 4 }).Count
    } while (($markers -lt 2 -or -not $received.EndsWith('>')) -and (Get-Date) -lt $deadline)
    if ($markers -lt 2 -or -not $received.EndsWith('>')) { throw "Timed out executing board code: $received" }
    $parts = $received.Split([char]4)
    if ($parts[1].Trim()) { throw "Board exception: $($parts[1])`nBoard output: $($parts[0] -replace '^OK', '')" }
    return ($parts[0] -replace '^OK', '').TrimEnd()
}

try {
    $connection.Open()
    Start-Sleep -Milliseconds 150
    if ($Action -eq 'Monitor') {
        # No REPL interrupt: leave the application and fan output running.
        $leaveRunning = $true
        $deadline = (Get-Date).AddSeconds($Seconds)
        do {
            Start-Sleep -Milliseconds 100
            $message = $connection.ReadExisting()
            if ($message) { Write-Output $message.TrimEnd() }
        } while ((Get-Date) -lt $deadline)
    } else {
        $connection.Write(([string][char]3 + [string][char]3))
        Start-Sleep -Milliseconds 150
        $connection.DiscardInBuffer()
        $connection.Write([string][char]1)
        Start-Sleep -Milliseconds 150
        $greeting = $connection.ReadExisting()
        if ($greeting -notmatch 'raw REPL') { throw "Raw REPL unavailable: $greeting" }
        $identity = Invoke-Raw "import machine, binascii`nprint(binascii.hexlify(machine.unique_id()).decode())"
        if ($identity.Trim() -ine $boardSerial) { throw 'Runtime board identity mismatch' }
        if ($Action -eq 'Info') {
            Invoke-Raw "import sys, os, gc`nprint(sys.version)`nprint(os.uname())`nprint('files:', os.listdir())`nprint('free_heap:', gc.mem_free())"
        } elseif ($Action -eq 'Exec') {
            if ($CodeFile) { $Code = Get-Content -LiteralPath $CodeFile -Raw }
            if (-not $Code) { throw 'Exec requires CodeFile or Code' }
            Invoke-Raw $Code $TimeoutSeconds
        } elseif ($Action -eq 'Deploy') {
            $deviceFiles = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'device') -File | Where-Object { $_.Extension -eq '.py' })
            if ($Only) {
                foreach ($requestedFile in $Only) {
                    if ($requestedFile -notin $deviceFiles.Name) { throw "Unknown device file: $requestedFile" }
                }
                $deviceFiles = @($deviceFiles | Where-Object { $_.Name -in $Only })
            }
            if (-not $deviceFiles.Count) { throw 'No device Python files to upload' }
            $backupDir = Join-Path $projectRoot ('artifacts\board-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            foreach ($deviceFile in $deviceFiles) {
                $remoteName = $deviceFile.Name
                if ($remoteName -notmatch '^[a-zA-Z0-9_]+\.py$') { throw 'Unexpected device filename' }
                $exists = Invoke-Raw "import os, binascii, gc`ngc.collect()`nprint('$remoteName' in os.listdir())"
                if ($exists -eq 'True') {
                    # Keep remote allocations small even after a long bench
                    # session has fragmented the MicroPython heap.
                    $previous = [IO.MemoryStream]::new()
                    try {
                        Invoke-Raw "_backup = open('$remoteName','rb')" | Out-Null
                        do {
                            $chunk = Invoke-Raw "print(binascii.b2a_base64(_backup.read(768)).decode().strip())"
                            if ($chunk) {
                                $decoded = [Convert]::FromBase64String($chunk)
                                $previous.Write($decoded, 0, $decoded.Length)
                            }
                        } while ($chunk)
                        Invoke-Raw '_backup.close()' | Out-Null
                        [IO.File]::WriteAllBytes((Join-Path $backupDir $remoteName), $previous.ToArray())
                    } finally {
                        $previous.Dispose()
                    }
                }
                $bytes = [IO.File]::ReadAllBytes($deviceFile.FullName)
                Invoke-Raw "_upload = open('$remoteName.upload','wb')" | Out-Null
                for ($offset = 0; $offset -lt $bytes.Length; $offset += 768) {
                    $base64 = [Convert]::ToBase64String($bytes, $offset, [Math]::Min(768, $bytes.Length - $offset))
                    Invoke-Raw "_upload.write(binascii.a2b_base64('$base64'))" | Out-Null
                }
                Invoke-Raw '_upload.close()' | Out-Null
                $remoteHash = Invoke-Raw "import hashlib`n_hash = hashlib.sha256()`nwith open('$remoteName.upload','rb') as _verified:`n while True:`n  _chunk = _verified.read(768)`n  if not _chunk: break`n  _hash.update(_chunk)`nprint(binascii.hexlify(_hash.digest()).decode())"
                $localHash = (Get-FileHash -LiteralPath $deviceFile.FullName -Algorithm SHA256).Hash
                if ($remoteHash -ine $localHash) { throw "Upload checksum mismatch: $remoteName" }
                Write-Output "Verified upload: $remoteName ($($bytes.Length) bytes)"
            }
            # Activate only after every staged file passed its readback checksum.
            foreach ($deviceFile in $deviceFiles | Sort-Object { if ($_.Name -eq 'main.py') { 1 } else { 0 } }) {
                $remoteName = $deviceFile.Name
                Invoke-Raw "os.rename('$remoteName.upload', '$remoteName')" | Out-Null
            }
            Invoke-Raw "os.sync()`nprint('Application files installed')"
        } elseif ($Action -eq 'Reboot') {
            Send-Code 'machine.reset()'
            $leaveRunning = $true
            Start-Sleep -Milliseconds 250
            Write-Output 'Project Pico reboot requested; monitor its startup after USB reconnects.'
        } elseif ($Action -eq 'Launch') {
            # Execute fresh modules without soft-resetting an unrelated device.
            $unloadCode = @'
import sys, rp2
# The app's Ctrl-C cleanup has already stopped its owned state machines.
# Reclaim exact known program objects before evicting their module references.
# MicroPython RP2350 v1.29 program fields 1..3 are per-PIO load offsets.
for _name, _symbol in (('sync_program','sync_program'), ('open_drain_pwm','_open_drain_program'), ('combined_program','combined_program')):
    _old = sys.modules.get(_name)
    if _old is not None:
        _program = getattr(_old, _symbol)
        for _block in range(3):
            if _program[1 + _block] >= 0:
                rp2.PIO(_block).remove_program(_program)
for _module in ('main','runtime','dashboard','portal','webpage','control','recipes','fan_settings','touch','touch_controls','pio_fan','combined_program','rig','ui','fan','sync_tach','sync_program','periods','open_drain_pwm','display','config'):
    sys.modules.pop(_module, None)
'@
            Invoke-Raw $unloadCode | Out-Null
            Send-Code "import main`nmain.run()"
            $leaveRunning = $true
            $deadline = (Get-Date).AddSeconds($Seconds)
            do {
                Start-Sleep -Milliseconds 100
                $message = $connection.ReadExisting()
                if ($message) {
                    Write-Output $message.Replace([string][char]4,'').TrimEnd()
                    if ($message.Contains([string][char]4)) {
                        $leaveRunning = $false
                        throw 'Application exited during launch; inspect the board output above.'
                    }
                }
            } while ((Get-Date) -lt $deadline)
        }
    }
} finally {
    if ($connection.IsOpen) {
        if (-not $leaveRunning) { $connection.Write([string][char]2) }
        $connection.Close()
    }
    $connection.Dispose()
}
