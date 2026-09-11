#ifndef SourceDir
  #error SourceDir must point to the staged BORING Console Community directory
#endif
#ifndef OutputDir
  #error OutputDir must point to the Windows artifact directory
#endif
#ifndef AppVersion
  #error AppVersion must come from controller_config.__version__
#endif

[Setup]
AppId={{A6A26BC2-8D59-43BB-84C6-7B62C89EAD90}
AppName=BORING Console Community
AppVersion={#AppVersion}
DefaultDirName={autopf}\BORING Console Community
DefaultGroupName=BORING Console Community
OutputDir={#OutputDir}
OutputBaseFilename=BORING-Console-Community-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayName=BORING Console Community
UninstallDisplayIcon={app}\BORING Console Community.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\BORING Console Community"; Filename: "{app}\BORING Console Community.exe"
Name: "{autodesktop}\BORING Console Community"; Filename: "{app}\BORING Console Community.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\BORING Console Community.exe"; Description: "Launch BORING Console Community"; Flags: nowait postinstall skipifsilent
