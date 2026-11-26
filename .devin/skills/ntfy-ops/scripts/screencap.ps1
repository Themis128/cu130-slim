param([string]$out = "C:\Users\tbaltzakis\devin-scripts\screen.png")
Add-Type -AssemblyName System.Windows.Forms
$b = New-Object System.Drawing.Bitmap([System.Windows.Forms.SystemInformation]::VirtualScreen.Width, [System.Windows.Forms.SystemInformation]::VirtualScreen.Height)
$g = [System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen([System.Windows.Forms.SystemInformation]::VirtualScreen.Left, [System.Windows.Forms.SystemInformation]::VirtualScreen.Top, 0, 0, $b.Size)
$b.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output "saved $($b.Width)x$($b.Height)"
