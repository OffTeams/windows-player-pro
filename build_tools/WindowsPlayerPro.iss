; Windows Player Pro — installer (Inno Setup 7).
; Per-user install (no administrator rights), so updates install silently without a UAC prompt.
; User data (%APPDATA%\WindowsPlayerPro: settings, history, playlist, downloads) is never touched.
#ifndef AppVersion
  #define AppVersion "1.0"
#endif
#ifndef AppEdition
  #define AppEdition "Open Beta"
#endif
#define AppName "Windows Player Pro"
#define Dist "..\build\dist\WindowsPlayerPro"

[Setup]
AppId={{6C3F2B9E-4C1D-4E7A-9B8F-2D5A7E1C0B44}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} ({#AppEdition})
AppPublisher=OFFTeams
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Qt 6 and Python 3.14 need Windows 10 or newer
MinVersion=10.0
OutputDir=..\build\installer
OutputBaseFilename=WindowsPlayerPro-{#AppVersion}-setup
SetupIconFile=..\assets\icons\win11.ico
UninstallDisplayIcon={app}\WindowsPlayerPro.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "helper"; Description: "Запускать помощника вместе с Windows (распознаёт ссылки в буфере обмена)"; GroupDescription: "Фоновый помощник:"
Name: "openwith"; Description: "Добавить плеер в «Открыть с помощью» для видео и аудио"; GroupDescription: "Файлы:"

[InstallDelete]
; icons of earlier versions (the name carries the version so Explorer's icon cache can't show a stale one)
Type: files; Name: "{app}\icon-*.ico"
; the previous git checkout of the code: its old packs mixed with the new history break git's maintenance
Type: filesandordirs; Name: "{app}\app\.git"

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\WindowsPlayerPro.exe"; IconFilename: "{app}\icon-{#AppVersion}.ico"; AppUserModelID: "WindowsPlayerPro.Player"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\WindowsPlayerPro.exe"; IconFilename: "{app}\icon-{#AppVersion}.ico"; AppUserModelID: "WindowsPlayerPro.Player"; Tasks: desktopicon

[Registry]
; same entry the app's own setting writes; Task Manager lists it under Startup apps
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Windows Player Pro Helper"; ValueData: """{app}\WPPHelper.exe"" --autostart"; Flags: uninsdeletevalue; Tasks: helper
Root: HKCU; Subkey: "Software\Classes\WindowsPlayerPro.Media"; ValueType: string; ValueData: "Медиафайл (Windows Player Pro)"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\WindowsPlayerPro.Media\DefaultIcon"; ValueType: string; ValueData: "{app}\icon-{#AppVersion}.ico"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\WindowsPlayerPro.Media\shell\open\command"; ValueType: string; ValueData: """{app}\WindowsPlayerPro.exe"" ""%1"""; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\WindowsPlayerPro.exe\shell\open\command"; ValueType: string; ValueData: """{app}\WindowsPlayerPro.exe"" ""%1"""; Flags: uninsdeletekey; Tasks: openwith
#define Ext(e) 'Root: HKCU; Subkey: "Software\Classes\.' + e + '\OpenWithProgids"; ValueType: string; ValueName: "WindowsPlayerPro.Media"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith'
#emit Ext("mkv")
#emit Ext("mp4")
#emit Ext("m4v")
#emit Ext("avi")
#emit Ext("mov")
#emit Ext("webm")
#emit Ext("wmv")
#emit Ext("ts")
#emit Ext("m2ts")
#emit Ext("mts")
#emit Ext("flv")
#emit Ext("mpg")
#emit Ext("mpeg")
#emit Ext("vob")
#emit Ext("3gp")
#emit Ext("mp3")
#emit Ext("flac")
#emit Ext("m4a")
#emit Ext("aac")
#emit Ext("ogg")
#emit Ext("opus")
#emit Ext("wav")
#emit Ext("wma")
#emit Ext("ape")
#emit Ext("dsf")
#emit Ext("ac3")
#emit Ext("dts")
#emit Ext("mka")
#emit Ext("m3u")
#emit Ext("m3u8")

[Run]
; also runs after a silent update, so the player comes back on its own
Filename: "{app}\WindowsPlayerPro.exe"; Description: "Запустить {#AppName}"; Flags: nowait postinstall

[UninstallDelete]
; the code folder is a git checkout; files brought in by later updates aren't in the install log
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\icon-*.ico"

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM WPPHelper.exe"; Flags: runhidden; RunOnceId: "KillHelper"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM WindowsPlayerPro.exe"; Flags: runhidden; RunOnceId: "KillPlayer"

[Code]
function IconStyle(Param: String): String;
var
  V: TWindowsVersion;
begin
  GetWindowsVersionEx(V);
  if (V.Major >= 10) and (V.Build >= 22000) then
    Result := 'win11'
  else if V.Major >= 10 then
    Result := 'win10'
  else
    Result := 'win7';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    { transparent NTFS compression (Windows Overlay Filter, LZX): files stay normal for the app and Windows,
      but take roughly half the disk space. On non-NTFS drives compact simply does nothing. }
    FileCopy(ExpandConstant('{app}\_internal\assets\icons\') + IconStyle('') + '.ico',
             ExpandConstant('{app}\icon-{#AppVersion}.ico'), False);
    WizardForm.StatusLabel.Caption := 'Сжатие файлов программы…';
    Exec(ExpandConstant('{sys}\compact.exe'), '/C /S /I /Q /EXE:LZX *', ExpandConstant('{app}'), SW_HIDE,
         ewWaitUntilTerminated, Code);
    { make Explorer reload icons for shortcuts that keep their path between versions }
    Exec(ExpandConstant('{sys}\ie4uinit.exe'), '-show', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { the helper and the player hold their files open; close both before replacing them }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM WPPHelper.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM WindowsPlayerPro.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
