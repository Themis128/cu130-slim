param([string]$topic, [int]$subY)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class UIZ {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, int dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  public static void Click(int x, int y) {
    SetCursorPos(x, y);
    System.Threading.Thread.Sleep(60);
    mouse_event(0x0002, 0, 0, 0, 0);
    mouse_event(0x0004, 0, 0, 0, 0);
  }
}
"@
$wshell = New-Object -ComObject wscript.shell
$p = Get-Process | Where-Object { $_.MainWindowTitle -match 'ntfy|cloudless' } | Select-Object -First 1
if ($p) { [UIZ]::SetForegroundWindow($p.MainWindowHandle) | Out-Null }
Start-Sleep -Milliseconds 400
[UIZ]::Click(147, $subY)
Start-Sleep -Milliseconds 1000
$wshell.SendKeys($topic)
Start-Sleep -Milliseconds 400
[UIZ]::Click(1257, 663)
Start-Sleep -Milliseconds 1300
Write-Output "pass $topic done"
