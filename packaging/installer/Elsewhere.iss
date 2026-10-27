; Build: iscc /DMyAppVersion=0.1.0 packaging\installer\Elsewhere.iss
#define MyAppName "Elsewhere"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppExeName "Elsewhere.exe"

[Setup]
AppId={{D64C6BE0-278D-4869-A4B9-EA76F2C13084}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=peago
AppPublisherURL=https://github.com/peagothedreamer/Elsewhere
AppSupportURL=https://github.com/peagothedreamer/Elsewhere/issues
DefaultDirName={localappdata}\Programs\Elsewhere
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=no
OutputDir=..\..\dist
OutputBaseFilename=Elsewhere-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\..\dist\Elsewhere\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSES\*"; DestDir: "{app}\LICENSES"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Elsewhere"; Flags: nowait postinstall skipifsilent
