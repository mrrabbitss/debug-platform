#ifndef SourceRoot
  #error SourceRoot is required.
#endif
#ifndef OutputRoot
  #error OutputRoot is required.
#endif
#ifndef AppVersion
  #define AppVersion "0.3.2"
#endif

[Setup]
AppId={{87293B10-EC31-41C9-B212-160C94883A91}
AppName=GWAP Debug Server
AppVersion={#AppVersion}
AppPublisher=mrrabbitss
DefaultDirName={localappdata}\Programs\GWAPDebugServer\app
DefaultGroupName=GWAP Debug Server
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#OutputRoot}
OutputBaseFilename=GWAP-Debug-Server-Setup-{#AppVersion}-x64
Compression=lzma2/normal
SolidCompression=no
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UninstallFilesDir={localappdata}\Programs\GWAPDebugServer\uninstall
UninstallDisplayIcon={app}\runtime\python\python.exe
VersionInfoVersion={#AppVersion}
VersionInfoProductName=GWAP Debug Server

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{tmp}\GWAPServerPayload"; Excludes: "package-manifest.json"; Flags: ignoreversion recursesubdirs createallsubdirs deleteafterinstall
Source: "{#SourceRoot}\package-manifest.json"; DestDir: "{tmp}\GWAPServerPayload"; Flags: ignoreversion deleteafterinstall; AfterInstall: InstallPayloadAtomically

[Icons]
Name: "{group}\启动服务器"; Filename: "{app}\start_server.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\GWAP 服务器"; Filename: "{app}\start_server.bat"; WorkingDir: "{app}"
Name: "{group}\备份服务器（先停止服务器）"; Filename: "{app}\backup_server.bat"; WorkingDir: "{app}"
Name: "{group}\打开数据目录"; Filename: "{app}\open_server_data.bat"; WorkingDir: "{app}"
Name: "{group}\服务器使用指南"; Filename: "{app}\服务器使用指南.md"

[Run]
Filename: "{app}\start_server.bat"; Description: "启动服务器"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Business data is outside this immutable application tree and is never removed.
Type: filesandordirs; Name: "{app}"; Check: IsExpectedAppRoot

[Code]
function IsExpectedAppRoot: Boolean;
begin
  Result := CompareText(AddBackslash(ExpandConstant('{app}')),
    AddBackslash(ExpandConstant('{localappdata}\Programs\GWAPDebugServer\app'))) = 0;
end;

procedure InstallPayloadAtomically;
var
  Parameters: String;
  ResultCode: Integer;
begin
  if not IsExpectedAppRoot then
    RaiseException('Refusing to install outside the server application directory.');
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\GWAPServerPayload\install_local.ps1') +
    '" -InstallRoot "' + ExpandConstant('{app}') +
    '" -NoLaunch -NoShortcuts -Components Full';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Parameters, ExpandConstant('{tmp}\GWAPServerPayload'), SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
    RaiseException('Unable to start the offline payload publisher.');
  if ResultCode <> 0 then
    RaiseException('Installation failed. The previous program and business data were preserved. Exit code: ' + IntToStr(ResultCode));
end;
