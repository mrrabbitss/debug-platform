#ifndef SourceRoot
  #error SourceRoot must point to the assembled offline package directory.
#endif
#ifndef OutputRoot
  #error OutputRoot must point to the installer artifact directory.
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

#define AppName "GWAP Debug Platform"
#define AppPublisher "mrrabbitss"
#define AppExeName "start.bat"

[Setup]
AppId={{61F92736-C499-46E0-BADF-561EB9A6D3C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\GWAPDebugPlatform
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir={#OutputRoot}
OutputBaseFilename=GWAP-Debug-Platform-Setup-{#AppVersion}-x64
Compression=lzma2/ultra64
SolidCompression=no
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\runtime\python\python.exe
; The launcher rejects every file that is not present in package-manifest.json.
; Keep Inno Setup's own uninstaller outside the immutable application tree.
UninstallFilesDir={localappdata}\Programs\GWAPDebugPlatform-Uninstall
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}

[Types]
Name: "full"; Description: "完整离线安装（Core + GGUF Embedding + GGUF Reranker）"
Name: "core"; Description: "仅平台 Core（Hashing Embedding / 不启用 Reranker）"
Name: "embedding"; Description: "Core + GGUF Embedding"
Name: "reranker"; Description: "Core + GGUF Reranker"
Name: "custom"; Description: "自定义组件"; Flags: iscustom

[Components]
Name: "core"; Description: "平台 Core（必选）"; Types: full core embedding reranker custom; Flags: fixed
Name: "retrieval"; Description: "本地检索组件（共享 CPU 运行时按需安装）"; Types: full embedding reranker
Name: "retrieval\embedding"; Description: "BGE GGUF Embedding"; Types: full embedding
Name: "retrieval\reranker"; Description: "Qwen3 GGUF Reranker"; Types: full reranker

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
; Never merge files directly into {app}. A merge leaves files removed by a
; newer release behind, which also violates package-manifest.json. Extract the
; complete payload to Setup's private temporary tree instead. The manifest is
; deliberately the final entry; its callback runs only after every payload file
; has been extracted successfully.
Source: "{#SourceRoot}\*"; DestDir: "{tmp}\GWAPDebugPlatformPayload"; Excludes: "package-manifest.json"; Flags: ignoreversion recursesubdirs createallsubdirs deleteafterinstall
Source: "{#SourceRoot}\package-manifest.json"; DestDir: "{tmp}\GWAPDebugPlatformPayload"; Flags: ignoreversion deleteafterinstall; AfterInstall: InstallPayloadAtomically

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\{#AppName} - CodeAgent"; Filename: "{app}\start_codeagent.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName} - CodeAgent"; Filename: "{app}\start_codeagent.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Runtime data is intentionally outside {app}, under
; %LOCALAPPDATA%\GWAPDebugPlatform, and is never removed here.
Type: filesandordirs; Name: "{app}"; Check: IsExpectedAppRoot

[Code]
function IsExpectedAppRoot: Boolean;
begin
  Result := CompareText(
    AddBackslash(ExpandConstant('{app}')),
    AddBackslash(ExpandConstant('{localappdata}\Programs\GWAPDebugPlatform'))
  ) = 0;
end;

procedure RegisterExtraCloseApplicationsResources;
begin
  // The payload is extracted to Setup's temporary tree, so these application
  // resources must be registered explicitly before the atomic directory swap.
  RegisterExtraCloseApplicationsResource(
    False,
    ExpandConstant('{app}\runtime\python\python.exe')
  );
  RegisterExtraCloseApplicationsResource(
    False,
    ExpandConstant('{app}\runtime\llama\llama-server.exe')
  );
end;

procedure InstallPayloadAtomically;
var
  PowerShellPath: String;
  PayloadRoot: String;
  InstallerScript: String;
  Parameters: String;
  ComponentSelection: String;
  ResultCode: Integer;
begin
  if not IsExpectedAppRoot then
    RaiseException('Refusing to install outside the managed application directory.');

  PayloadRoot := ExpandConstant('{tmp}\GWAPDebugPlatformPayload');
  InstallerScript := PayloadRoot + '\install_local.ps1';
  PowerShellPath := ExpandConstant(
    '{sys}\WindowsPowerShell\v1.0\powershell.exe'
  );
  ComponentSelection := 'Core';
  if WizardIsComponentSelected('retrieval\embedding') then
    ComponentSelection := 'Embedding';
  if WizardIsComponentSelected('retrieval\reranker') then
  begin
    if ComponentSelection = 'Embedding' then
      ComponentSelection := 'Full'
    else
      ComponentSelection := 'Reranker';
  end;
  Parameters :=
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    InstallerScript + '" -InstallRoot "' + ExpandConstant('{app}') +
    '" -NoLaunch -NoShortcuts -Components ' + ComponentSelection;

  Log('Publishing the verified payload through install_local.ps1.');
  if not Exec(
    PowerShellPath,
    Parameters,
    PayloadRoot,
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    RaiseException(
      'Unable to start the atomic payload publisher: ' +
      SysErrorMessage(ResultCode)
    );

  if ResultCode <> 0 then
    RaiseException(
      'Atomic payload publication failed with exit code ' +
      IntToStr(ResultCode) + '. The previous application tree was preserved.'
    );
end;
