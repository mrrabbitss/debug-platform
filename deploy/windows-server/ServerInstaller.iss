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
DefaultDirName={code:GetReleaseDirectory}
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
Source: "{#SourceRoot}\*"; DestDir: "{tmp}\GWAPServerPayload"; Flags: dontcopy recursesubdirs createallsubdirs

[Icons]
Name: "{group}\启动服务器"; Filename: "{app}\start_server.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\GWAP 服务器"; Filename: "{app}\start_server.bat"; WorkingDir: "{app}"
Name: "{group}\备份服务器（先停止服务器）"; Filename: "{app}\backup_server.bat"; WorkingDir: "{app}"
Name: "{group}\打开数据目录"; Filename: "{app}\open_server_data.bat"; WorkingDir: "{app}"
Name: "{group}\恢复管理员访问（先停止服务器）"; Filename: "{app}\Recover Administrator Access.bat"; WorkingDir: "{app}"
Name: "{group}\查看内置组网 Skill"; Filename: "{app}\bundled-knowledge\hilink-diag"; Check: DirExists(ExpandConstant('{app}\bundled-knowledge\hilink-diag'))
Name: "{group}\服务器使用指南"; Filename: "{app}\服务器使用指南.md"

[Run]
Filename: "{app}\start_server.bat"; Description: "启动服务器"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Business data is outside this immutable application tree and is never removed.
Type: filesandordirs; Name: "{app}"; Check: IsExpectedAppRoot

[Code]
var
  ReleaseDirectory: String;
  PayloadExtracted: Boolean;
  PreviousAttemptFailed: Boolean;

function GetReleaseDirectory(Param: String): String;
var
  Base: String;
  Suffix: Integer;
begin
  if ReleaseDirectory = '' then begin
    Base := ExpandConstant('{localappdata}\Programs\GWAPDebugServer\releases\{#AppVersion}-') +
      GetDateTimeString('yyyymmdd-hhnnss-zzz', '-', '-');
    ReleaseDirectory := Base;
    Suffix := 0;
    while DirExists(ReleaseDirectory) do begin
      Suffix := Suffix + 1;
      ReleaseDirectory := Base + '-' + IntToStr(Suffix);
    end;
  end;
  Result := ReleaseDirectory;
end;

function IsExpectedAppRoot: Boolean;
begin
  Result := (CompareText(AddBackslash(ExtractFileDir(ExpandConstant('{app}'))),
    AddBackslash(ExpandConstant('{localappdata}\Programs\GWAPDebugServer\releases'))) = 0) and
    (Pos('{#AppVersion}-', ExtractFileName(ExpandConstant('{app}'))) = 1);
end;

procedure InstallServerRelease;
var
  FailureLog: String;
  FailureSummary: String;
  FailureReason: String;
  FailureReasonUtf8: AnsiString;
  LogDirectory: String;
  Parameters: String;
  ResultCode: Integer;
begin
  if (not IsExpectedAppRoot) or (CompareText(ExpandConstant('{app}'), GetReleaseDirectory('')) <> 0) then
    RaiseException('Refusing to install outside the server application directory.');
  WizardForm.StatusLabel.Caption := '正在校验完整程序并更新六份组网 Skill，请等待索引完成…';
  LogDirectory := ExpandConstant('{localappdata}\GWAPDebugServer\install-logs');
  FailureLog := LogDirectory + '\setup-payload-' +
    GetDateTimeString('yyyymmdd-hhnnss', '-', '-') + '.log';
  FailureSummary := FailureLog + '.summary.txt';
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\GWAPServerPayload\publish_server_payload.ps1') +
    '" -PayloadRoot "' + ExpandConstant('{tmp}\GWAPServerPayload') +
    '" -InstallRoot "' + ExpandConstant('{app}') +
    '" -FailureLog "' + FailureLog +
    '" -FailureSummary "' + FailureSummary + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Parameters, ExpandConstant('{tmp}\GWAPServerPayload'), SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
    RaiseException('Unable to start the offline payload publisher.');
  if ResultCode <> 0 then begin
    FailureReason := 'The offline payload publisher exited with code ' + IntToStr(ResultCode) + '.';
    if FileExists(FailureLog) then begin
      if LoadStringFromFile(FailureSummary, FailureReasonUtf8) then begin
        FailureReason := Trim(UTF8Decode(FailureReasonUtf8));
        if FailureReason = '' then
          FailureReason := 'The offline payload publisher exited with code ' + IntToStr(ResultCode) + '.';
      end;
      RaiseException('Installation failed. ' + FailureReason + #13#10 + #13#10 +
        'Publisher output was saved for this Windows account at:' + #13#10 + FailureLog + #13#10 + #13#10 +
        'Previous application directories were not replaced. Review the saved installation and knowledge-update log.');
    end;
    RaiseException('Installation failed. ' + FailureReason + #13#10 + #13#10 +
      'The publisher diagnostic could not be saved. Review the Setup log at:' + #13#10 +
      ExpandConstant('{log}') + #13#10 + #13#10 +
      'Previous application directories were not replaced.');
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  try
    if PreviousAttemptFailed then begin
      ReleaseDirectory := '';
      WizardForm.DirEdit.Text := GetReleaseDirectory('');
    end;
    if not PayloadExtracted then begin
      ExtractTemporaryFiles('{tmp}\GWAPServerPayload\*');
      PayloadExtracted := True;
    end;
    InstallServerRelease;
    PreviousAttemptFailed := False;
  except
    PreviousAttemptFailed := True;
    Result := GetExceptionMessage;
  end;
end;
