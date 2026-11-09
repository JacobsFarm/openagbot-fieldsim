; Inno Setup-script: maakt dist/openagbot-fieldsim-setup.exe van dist/openagbot-fieldsim/ (eerst packaging/build_exe.py draaien).
#define AppName "OpenAgbot FieldSim"
#define AppVersion "0.1.0"

[Setup]
AppId={{4B5E1F2A-7C1D-4C8E-9C61-5A0B1F0E2D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bruut OpenAgbot
DefaultDirName={localappdata}\Programs\openagbot-fieldsim
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=openagbot-fieldsim-setup
Compression=lzma2
SolidCompression=yes
LicenseFile=
UninstallDisplayIcon={app}\openagbot-fieldsim.exe

[Files]
Source: "..\dist\openagbot-fieldsim\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\OpenAgbot FieldSim"; Filename: "{app}\openagbot-fieldsim.exe"
Name: "{userdesktop}\OpenAgbot FieldSim"; Filename: "{app}\openagbot-fieldsim.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Snelkoppeling op het bureaublad"; GroupDescription: "Extra:"

[Run]
Filename: "{app}\openagbot-fieldsim.exe"; Description: "OpenAgbot FieldSim starten"; Flags: nowait postinstall skipifsilent
