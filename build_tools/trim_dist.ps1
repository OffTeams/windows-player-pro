# Removes parts of the PyInstaller output the app never uses, to keep the install small.
param([string]$Dist = "$PSScriptRoot\..\build\dist\WindowsPlayerPro")
$ErrorActionPreference = 'Stop'
$qt = Join-Path $Dist '_internal\PySide6'
$before = (Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum
# DevTools (the hidden sniffer browser never opens them) and debug symbols
Get-ChildItem "$qt\resources" -File | Where-Object { $_.Name -like '*devtools*' -or $_.Name -like '*.debug.*' } | Remove-Item -Force
# software OpenGL fallback: video goes through Direct3D 11 and the sniffer page is never drawn
Remove-Item "$qt\opengl32sw.dll" -Force -ErrorAction SilentlyContinue
# translations: Russian and English only
Get-ChildItem "$qt\translations" -File -Filter *.qm | Where-Object { $_.BaseName -notmatch '_(ru|en)$' } | Remove-Item -Force
Get-ChildItem "$qt\translations\qtwebengine_locales" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.BaseName -notin 'ru', 'en-US', 'en-GB' } | Remove-Item -Force
$after = (Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum
'{0:N0} MB -> {1:N0} MB' -f ($before / 1MB), ($after / 1MB)
