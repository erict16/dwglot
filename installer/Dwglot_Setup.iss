; 图译 — Inno Setup 6. ODA is not in this payload.
; Compile via installer\build_installer.ps1 so MyAppVersion comes from backend\app_meta.py.

#define MyAppName "图译"
#ifndef MyAppVersion
#define MyAppVersion "0.1.2"
#endif
#define MyAppPublisher "Eric Tan"
#define MyAppExeName "Tuyi.exe"
#define MyAppURL "https://github.com/erict16/tuyi"

[Setup]
AppId={{B3F91C4A-2D7E-4A18-9C55-8E1D0A7B6F32}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\Tuyi
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=Tuyi_v{#MyAppVersion}_Setup
SetupIconFile=..\ico.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
DisableProgramGroupPage=yes
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
DisableReadyPage=no
DisableDirPage=no
UsePreviousAppDir=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
; onedir: Tuyi.exe + tuyi-cli.exe share {app}\_internal
Source: "..\dist\Tuyi\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Tuyi\tuyi-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Tuyi\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
