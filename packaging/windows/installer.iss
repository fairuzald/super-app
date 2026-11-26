; Inno Setup script: turns dist\windows\super-app into "Super App Setup <version>.exe".
;   iscc /DAppVersion=1.0.0 packaging\windows\installer.iss
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B7C3E52-9A1F-4C0E-8E43-5D2B1A7F9C10}
AppName=Super App
AppVersion={#AppVersion}
AppPublisher=Super App contributors
AppPublisherURL=https://github.com/fairuzald/super-app
DefaultDirName={autopf}\Super App
DefaultGroupName=Super App
UninstallDisplayIcon={app}\super-app.exe
SetupIconFile=super-app.ico
OutputDir=..\..\dist
OutputBaseFilename=Super-App-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\windows\super-app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Super App"; Filename: "{app}\super-app.exe"
Name: "{group}\Uninstall Super App"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Super App"; Filename: "{app}\super-app.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\super-app.exe"; Description: "{cm:LaunchProgram,Super App}"; Flags: nowait postinstall skipifsilent
