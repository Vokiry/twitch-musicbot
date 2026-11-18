param(
    [Parameter(Position=0)]
    [ValidateSet("pause", "play", "toggle")]
    [string]$Action = "pause"
)

try {
    # Activate Windows Runtime API for System Media Transport Controls (SMTC)
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
    $op = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()

    # Wait for the async manager initialization
    $timeoutCounter = 0
    while (($op.Status -eq 0 -or $op.Status -eq "Started") -and $timeoutCounter -lt 40) {
        Start-Sleep -Milliseconds 25
        $timeoutCounter++
    }

    $mgr = $op.GetResults()
    $session = $mgr.GetCurrentSession()

    if ($null -ne $session) {
        switch ($Action.ToLower()) {
            "pause" {
                $null = $session.TryPauseAsync()
            }
            "play" {
                $null = $session.TryPlayAsync()
            }
            "toggle" {
                $null = $session.TryTogglePlayPauseAsync()
            }
        }
    }
} catch {
    # Fallback to virtual key VK_MEDIA_PLAY_PAUSE (0xB3) if WinRT is unavailable
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinMediaFallback {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, uint dwExtraInfo);
    public static void Toggle() {
        keybd_event(0xB3, 0, 0, 0);
        keybd_event(0xB3, 0, 2, 0);
    }
}
"@ -ErrorAction SilentlyContinue
    [WinMediaFallback]::Toggle()
}
