param([string]$topic)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class UIX {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, int dwExtraInfo);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  public static void Click(int x, int y) {
    SetCursorPos(x, y);
    System.Threading.Thread.Sleep(60);
    mouse_event(0x0002, 0, 0, 0, 0);
    mouse_event(0x0004, 0, 0, 0, 0);
  }
}
public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
"@
$wshell = New-Object -ComObject wscript.shell
$p = Get-Process | Where-Object { $_.MainWindowTitle -eq 'ntfy' } | Select-Object -First 1
if (-not $p) { Write-Output 'NO-WINDOW'; exit }
[UIX]::ShowWindow($p.MainWindowHandle, 3) | Out-Null   # SW_MAXIMIZE
Start-Sleep -Milliseconds 500
[UIX]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 500
$r = New-Object RECT
[UIX]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
Write-Output "rect $($r.Left),$($r.Top) ${w}x${h}"
# ratios from verified maximized layout
$sx = $r.Left + [int](0.076 * $w); $sy = $r.Top + [int](0.350 * $h)   # subscribe sidebar item
$bx = $r.Left + [int](0.653 * $w); $by = $r.Top + [int](0.640 * $h)   # SUBSCRIBE button
[UIX]::Click($sx, $sy)
Start-Sleep -Milliseconds 1000
$wshell.SendKeys($topic)
Start-Sleep -Milliseconds 400
[UIX]::Click($bx, $by)
Start-Sleep -Milliseconds 1200
Write-Output "pass $topic done"
