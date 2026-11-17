param(
    [ValidateSet('wifi', 'bluetooth')][string]$Kind,
    [ValidateSet('on', 'off', 'status')][string]$State
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType=WindowsRuntime]
    $null = [Windows.Devices.Radios.RadioAccessStatus, Windows.System.Devices, ContentType=WindowsRuntime]
    $null = [Windows.Devices.Radios.RadioState, Windows.System.Devices, ContentType=WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-Radio($operation, $type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
        if (!$task.Wait(8000)) { throw 'Radio operation timed out.' }
        return $task.Result
    }
    if ($State -ne 'status') {
        $access = Await-Radio ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
        if ($access -ne 'Allowed') { throw "Radio access is $access. Check Windows permissions or administrator policy." }
    }
    $radios = Await-Radio ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
    $matching = @($radios | Where-Object { $_.Kind.ToString() -eq $Kind })
    if (!$matching.Count) { throw "No $Kind radio is available. Check its driver and hardware switch in Windows Settings." }
    foreach ($radio in $matching) {
        if ($State -ne 'status') {
            $target = if ($State -eq 'on') { [Windows.Devices.Radios.RadioState]::On } else { [Windows.Devices.Radios.RadioState]::Off }
            $result = Await-Radio ($radio.SetStateAsync($target)) ([Windows.Devices.Radios.RadioAccessStatus])
            if ($result -ne 'Allowed') { throw "Windows denied the change: $result" }
            $deadline = (Get-Date).AddSeconds(3)
            while ($radio.State -ne $target -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
            if ($radio.State -ne $target) { throw "$($radio.Name) is still $($radio.State); Windows did not apply $State. Check the hardware switch, airplane mode, and device policy." }
        }
        Write-Output "Verified $($radio.Name): $($radio.State)"
    }
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
