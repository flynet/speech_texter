; VoiceType installer (Inno Setup 6)
;
; Build with:
;   iscc /DMyAppVersion=0.3.0 installer\voicetype.iss
;
; The script expects a PyInstaller onedir build in dist\VoiceType, including the
; bundled models directory (models\small).

#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif

#define MyAppName "VoiceType"
#define MyAppPublisher "flynet"
#define MyAppURL "https://github.com/flynet/speech_texter"
#define MyAppExeName "VoiceType.exe"
#define MyAppId "{{B6B0B3F4-6C8E-4B9A-9E4F-2A7C1D5E8F30}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir=..\dist-installer
OutputBaseFilename=VoiceType-Setup-{#MyAppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no
AppMutex=VoiceType.SingleInstance
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start {#MyAppName} with Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\VoiceType\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoiceType"; ValueData: """{app}\{#MyAppExeName}"""; flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The application can enable autostart at runtime, so always clean the Run value.
Filename: "{sys}\reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v VoiceType /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"

[Code]
function InitializeSetup(): Boolean;
var
  Attempt: Integer;
begin
  { Give an updating instance a few seconds to exit before the mutex check. }
  for Attempt := 1 to 25 do
  begin
    if not CheckForMutexes('VoiceType.SingleInstance') then
      Break;
    Sleep(200);
  end;
  Result := True;
end;
