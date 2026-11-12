; Inno Setup script for velocity's Windows installer (decided 2026-11-11). Built by
; packaging/build_windows.ps1 after PyInstaller; not meant to be run by hand.
;
; Installs for the current user only (no administrator rights), into
; %LOCALAPPDATA%\Programs\velocity, with a Start menu shortcut and an uninstaller. Your
; settings, sessions and keys are not part of the install: uninstalling leaves them.
; Unsigned: Windows SmartScreen warns once ("More info" -> "Run anyway").

#define AppVersion GetEnv("VELOCITY_VERSION")

[Setup]
AppId={{6F1D2C3B-8E4A-4B7F-9C2D-5A1E7B3F9D40}
AppName=velocity
AppVersion={#AppVersion}
AppPublisher=sova-nova
AppPublisherURL=https://github.com/sova-nova/velocity-framework
DefaultDirName={localappdata}\Programs\velocity
DefaultGroupName=velocity
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\build
OutputBaseFilename=velocity-{#AppVersion}-windows-setup
SetupIconFile=velocity.ico
UninstallDisplayIcon={app}\velocity.exe
UninstallDisplayName=velocity
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Add a shortcut on the desktop"; Flags: unchecked

[Files]
Source: "..\build\dist\velocity\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\velocity"; Filename: "{app}\velocity.exe"; Comment: "A tutor for any subject"
Name: "{userdesktop}\velocity"; Filename: "{app}\velocity.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\velocity.exe"; Description: "Open velocity"; Flags: nowait postinstall skipifsilent
