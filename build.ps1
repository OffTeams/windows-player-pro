# Builds build\dist\WindowsPlayerPro (two exes) and build\installer\WindowsPlayerPro-<ver>-setup.exe.
# Needs: setup.ps1 done (vendor\), PyInstaller in .venv, Inno Setup 7 (ISCC.exe) in build\tools\inno or on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = '.venv\Scripts\python.exe'
$ver = & $py -c "import wpp; print(wpp.__version__)"
$edition = & $py -c "import wpp; print(wpp.EDITION)"
Write-Host "Windows Player Pro $ver ($edition)"
& $py tools\make_icons.py | Out-Null
# git keeps its pack files read-only; PyInstaller can't clear the previous output with them in it
if (Test-Path 'build\dist\WindowsPlayerPro') { Remove-Item 'build\dist\WindowsPlayerPro' -Recurse -Force }
& $py -m PyInstaller build_tools\WindowsPlayerPro.spec --noconfirm --distpath build\dist --workpath build\work --log-level ERROR
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
& "$PSScriptRoot\build_tools\trim_dist.ps1"
# the player's code ships as a git checkout so the installed app updates with `git pull`
$git = 'vendor\git\cmd\git.exe'
$app = 'build\dist\WindowsPlayerPro\app'
if (& $git -c safe.directory=* status --porcelain --untracked-files=no) { throw 'Commit your changes first: the installer ships the committed code' }
$src = 'file:///' + ($PSScriptRoot -replace '\\', '/')
& $git -c safe.directory=* clone --quiet --depth 1 --branch main --no-local $src $app
if ($LASTEXITCODE) { throw 'git clone failed' }
& $git -c safe.directory=* -C $app remote set-url origin https://github.com/OffTeams/windows-player-pro.git
& $git -c safe.directory=* -C $app config core.autocrlf true
# git marks .git hidden, and Inno Setup skips hidden files: without this the installed copy can't update
attrib -h "$app\.git" /S /D | Out-Null
attrib -h "$app\.git"
$iscc = @('build\tools\inno\ISCC.exe', "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe", "$env:ProgramFiles\Inno Setup 7\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction Stop).Source }
& $iscc /Qp "/DAppVersion=$ver" "/DAppEdition=$edition" build_tools\WindowsPlayerPro.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
Get-ChildItem build\installer\*.exe | Select-Object Name, @{n = 'MB'; e = { [int]($_.Length / 1MB) } }
