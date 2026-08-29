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
DefaultDirName={sd}\Apps\Tuyi
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=Output
OutputBaseFilename=Tuyi_v{#MyAppVersion}_Setup
SetupIconFile=..\ico.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
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
ShowLanguageDialog=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Messages]
WelcomeLabel1=欢迎安装 图译
WelcomeLabel2=图译把 DWG / DXF 里的文字译出来。%n%n这是未签名的 v{#MyAppVersion}。若 Windows 弹出 SmartScreen，点「更多信息」，再点「仍要运行」。%n%nDWG 需要本机已装 ODA File Converter，本安装包不含 ODA。
FinishedHeadingLabel=图译已装好
FinishedLabel=可以开始用了。DWG 需要本机已装 ODA File Converter。
ClickFinish=点「完成」启动 图译。

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "附加图标："; Flags: checkedonce

[Files]
; onedir: Tuyi.exe + tuyi-cli.exe share {app}\_internal
Source: "..\dist\Tuyi\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Tuyi\tuyi-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Tuyi\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
