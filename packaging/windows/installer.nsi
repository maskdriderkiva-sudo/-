; GBForge Windows インストーラー (NSIS)
; makensis -DVERSION=0.1.0 -DSRCDIR=<ステージング> -DOUTFILE=<出力> installer.nsi
Unicode true
SetCompressor /SOLID lzma
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.1.0"
!endif
!define APPNAME "GBForge"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\GBForge"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\GBForge"
InstallDirRegKey HKCU "Software\GBForge" "InstallDir"
RequestExecutionLevel user          ; 管理者権限不要 (ユーザーごとにインストール)
BrandingText "GBForge — ゲームボーイ用ゲームエンジン"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "GBForge"
VIAddVersionKey "FileDescription" "GBForge Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "GBForge"

!define MUI_ICON "gbforge.ico"
!define MUI_UNICON "gbforge.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "GBForge ${VERSION} のセットアップ"
!define MUI_WELCOMEPAGE_TEXT "ゲームボーイ / ゲームボーイカラー用のゲームエンジン & エディタです。$\r$\n$\r$\n次のものがすべて含まれています:$\r$\n  ・GBDK-2020 4.3.0 (C コンパイラ)$\r$\n  ・日本語フォント (美咲フォント) と gbjp ライブラリ$\r$\n  ・ロムライタ (CUBIC STYLE / GB Cart Flasher) ドライバ$\r$\n  ・エミュレータ (PyBoy)$\r$\n$\r$\n管理者権限は不要です。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\GBForge.exe"
!define MUI_FINISHPAGE_RUN_TEXT "GBForge を起動する"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"

Section "GBForge 本体 (必須)" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; 古いバージョンの Python/GBDK を残さない
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\gbdk"
  File /r "${SRCDIR}\*.*"
  WriteRegStr HKCU "Software\GBForge" "InstallDir" "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\GBForge"
  CreateShortcut "$SMPROGRAMS\GBForge\GBForge.lnk" "$INSTDIR\GBForge.exe" "" "$INSTDIR\GBForge.exe" 0
  CreateShortcut "$SMPROGRAMS\GBForge\GBForge コマンドプロンプト.lnk" "$SYSDIR\cmd.exe" '/k "$INSTDIR\gbforge-shell.cmd"' "$INSTDIR\GBForge.exe" 0
  CreateShortcut "$SMPROGRAMS\GBForge\README.lnk" "$INSTDIR\README.html"
  CreateShortcut "$SMPROGRAMS\GBForge\アンインストール.lnk" "$INSTDIR\Uninstall.exe"

  ; アンインストール情報 (設定 → アプリ に表示)
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "GBForge ${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "GBForge"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\GBForge.exe"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

Section "デスクトップにショートカット" SecDesktop
  CreateShortcut "$DESKTOP\GBForge.lnk" "$INSTDIR\GBForge.exe" "" "$INSTDIR\GBForge.exe" 0
SectionEnd

Section "project.gbproj を GBForge で開く (関連付け)" SecAssoc
  WriteRegStr HKCU "Software\Classes\.gbproj" "" "GBForge.Project"
  WriteRegStr HKCU "Software\Classes\GBForge.Project" "" "GBForge プロジェクト"
  WriteRegStr HKCU "Software\Classes\GBForge.Project\DefaultIcon" "" "$INSTDIR\GBForge.exe,0"
  WriteRegStr HKCU "Software\Classes\GBForge.Project\shell\open\command" "" '"$INSTDIR\GBForge.exe" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "エディタ・GBDK-2020・日本語フォント・ロムライタドライバ・エミュレータ"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "デスクトップに GBForge のアイコンを作ります"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAssoc} "エクスプローラーで project.gbproj をダブルクリックするとエディタで開きます"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\gbdk"
  RMDir /r "$INSTDIR\licenses"
  Delete "$INSTDIR\GBForge.exe"
  Delete "$INSTDIR\gbforge-shell.cmd"
  Delete "$INSTDIR\README.html"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  RMDir /r "$SMPROGRAMS\GBForge"
  Delete "$DESKTOP\GBForge.lnk"
  ReadRegStr $0 HKCU "Software\Classes\.gbproj" ""
  StrCmp $0 "GBForge.Project" 0 +3
    DeleteRegKey HKCU "Software\Classes\.gbproj"
    DeleteRegKey HKCU "Software\Classes\GBForge.Project"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\GBForge"
SectionEnd
