# Patches Antigravity shortcuts to launch with a CDP port so agy-usage can inject its panel.
$ErrorActionPreference = 'Continue'
$sh = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path $env:USERPROFILE 'Desktop\Antigravity.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Antigravity.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Antigravity.lnk')
)
foreach ($t in $targets) {
  if (Test-Path $t) {
    $s = $sh.CreateShortcut($t)
    $s.Arguments = '--remote-debugging-port=0'
    $s.Save()
    "patched: $t"
  } else {
    "missing: $t"
  }
}
"Restart Antigravity for the change to take effect."
