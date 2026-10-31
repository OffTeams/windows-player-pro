# Windows Player Pro: one-time setup.
# Creates .venv, installs Python packages, downloads libmpv, FFmpeg and MinGit into vendor\,
# and puts a "Windows Player Pro" shortcut on the desktop.
# Run:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location $PSScriptRoot
$dl = Join-Path $PSScriptRoot 'build\dl'
$vendor = Join-Path $PSScriptRoot 'vendor'
New-Item -ItemType Directory -Force $dl, $vendor | Out-Null
$gh = @{ 'User-Agent' = 'windows-player-pro-setup' }

function Asset($repo, $pattern) {
    $r = Invoke-RestMethod -Headers $gh "https://api.github.com/repos/$repo/releases/latest"
    $a = $r.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
    if (-not $a) { throw "В релизе $repo нет файла $pattern" }
    return $a
}

Write-Host '1/4  Python-окружение'
if (-not (Test-Path .venv\Scripts\python.exe)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv } else { & python -m venv .venv }
}
& .venv\Scripts\python.exe -m pip install -q --upgrade pip
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt

Write-Host '2/4  libmpv (shinchiro/mpv-winbuild-cmake)'
if (-not (Test-Path "$vendor\libmpv-2.dll")) {
    $a = Asset 'shinchiro/mpv-winbuild-cmake' '^mpv-dev-x86_64-\d{8}-git-[0-9a-f]+\.7z$'
    Invoke-WebRequest $a.browser_download_url -OutFile "$dl\mpv-dev.7z"
    $7z = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $7z) {
        Invoke-WebRequest 'https://www.7-zip.org/a/7zr.exe' -OutFile "$dl\7zr.exe"
        $7z = "$dl\7zr.exe"
    }
    & $7z e "$dl\mpv-dev.7z" "-o$vendor" libmpv-2.dll -r -y | Out-Null
}

Write-Host '3/4  FFmpeg (BtbN/FFmpeg-Builds), Deno (denoland/deno) и MinGit (git-for-windows)'
if (-not (Test-Path "$vendor\ffmpeg.exe")) {
    $a = Asset 'BtbN/FFmpeg-Builds' 'win64-gpl\.zip$'
    Invoke-WebRequest $a.browser_download_url -OutFile "$dl\ffmpeg.zip"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [IO.Compression.ZipFile]::OpenRead("$dl\ffmpeg.zip")
    foreach ($e in $z.Entries) {
        if ($e.Name -in 'ffmpeg.exe', 'ffprobe.exe') {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $vendor $e.Name), $true)
        }
    }
    $z.Dispose()
}
if (-not (Test-Path "$vendor\deno.exe")) {
    # JavaScript runtime that yt-dlp needs for YouTube
    $a = Asset 'denoland/deno' '^deno-x86_64-pc-windows-msvc\.zip$'
    Invoke-WebRequest $a.browser_download_url -OutFile "$dl\deno.zip"
    Expand-Archive "$dl\deno.zip" -DestinationPath $vendor -Force
}
if (-not (Test-Path "$vendor\git\cmd\git.exe")) {
    $a = Asset 'git-for-windows/git' '^MinGit-[\d.]+-64-bit\.zip$'
    Invoke-WebRequest $a.browser_download_url -OutFile "$dl\mingit.zip"
    Expand-Archive "$dl\mingit.zip" -DestinationPath "$vendor\git" -Force
}

Write-Host '4/4  Ярлык на рабочем столе'
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Windows Player Pro.lnk'))
$sh.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$sh.Arguments = '"' + (Join-Path $PSScriptRoot 'main.py') + '"'
$sh.WorkingDirectory = $PSScriptRoot
$sh.Description = 'Windows Player Pro'
$sh.Save()
Write-Host 'Готово. Запуск: ярлык «Windows Player Pro» на рабочем столе или run.bat'
