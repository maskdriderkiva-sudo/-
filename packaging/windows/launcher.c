/*
 * GBForge の Windows 版ランチャー。同じソースから 2 つの exe を作る。
 *
 * GBForge.exe (GBF_GUI を定義してビルド、Windows アプリ = コンソール画面なし)
 *   python\pythonw.exe で `python -m gbforge app <引数>` を実行し、ネイティブアプリのウィンドウを開く。
 *   GBForge.exe                       → スタート画面
 *   GBForge.exe C:\...\project.gbproj   → そのプロジェクトを開く
 *
 * GBForgeCLI.exe (コンソールアプリ)
 *   python\python.exe で `python -m gbforge <引数>` を実行する。Visual Studio / VS Code のビルド・コマンドプロンプト用。
 *   GBForgeCLI.exe build MyGame
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>

static const wchar_t *skip_argv0(const wchar_t *cmd) {
    if (*cmd == L'"') {
        cmd++;
        while (*cmd && *cmd != L'"') cmd++;
        if (*cmd) cmd++;
    } else {
        while (*cmd && *cmd != L' ' && *cmd != L'\t') cmd++;
    }
    while (*cmd == L' ' || *cmd == L'\t') cmd++;
    return cmd;
}

#ifdef GBF_GUI
int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmd, int show) {
#else
int wmain(void) {
#endif
    wchar_t exe[MAX_PATH], dir[MAX_PATH], python[MAX_PATH], cli[MAX_PATH];
    wchar_t *cmdline;
    const wchar_t *args;
    size_t len;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD code = 1;

    GetModuleFileNameW(NULL, exe, MAX_PATH);
    wcscpy(dir, exe);
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
#ifdef GBF_GUI
    _snwprintf(python, MAX_PATH, L"%s\\python\\pythonw.exe", dir);
#else
    _snwprintf(python, MAX_PATH, L"%s\\python\\python.exe", dir);
#endif
    /* IDE やエミュレーターの起動など、GBForge 自身を呼び出すときはコンソール版を使う */
    _snwprintf(cli, MAX_PATH, L"%s\\GBForgeCLI.exe", dir);

    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"python\\python.exe が見つかりません。GBForge を再インストールしてください。",
                    L"GBForge", MB_ICONERROR);
        return 1;
    }
    SetEnvironmentVariableW(L"GBFORGE_APP_DIR", dir);
    SetEnvironmentVariableW(L"GBFORGE_LAUNCHER", GetFileAttributesW(cli) != INVALID_FILE_ATTRIBUTES ? cli : exe);

    args = skip_argv0(GetCommandLineW());
    len = wcslen(python) + wcslen(args) + 64;
    cmdline = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, len * sizeof(wchar_t));
    /* -E: PYTHON* 環境変数を無視, -s: ユーザー site-packages を無視 (他の Python と干渉しない) */
#ifdef GBF_GUI
    _snwprintf(cmdline, len, L"\"%s\" -E -s -m gbforge app %s", python, args);
#else
    _snwprintf(cmdline, len, L"\"%s\" -E -s -m gbforge %s", python, args);
#endif

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    {
        /* 標準入出力が無い起動方法 (サービス・一部の IDE など) でも Python が起動できるよう NUL を渡す */
        HANDLE in = GetStdHandle(STD_INPUT_HANDLE), out = GetStdHandle(STD_OUTPUT_HANDLE), err = GetStdHandle(STD_ERROR_HANDLE);
        SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
        if (!in || in == INVALID_HANDLE_VALUE || GetFileType(in) == FILE_TYPE_UNKNOWN ||
            !out || out == INVALID_HANDLE_VALUE || !err || err == INVALID_HANDLE_VALUE) {
            HANDLE nul_in = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, NULL);
            HANDLE nul_out = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, NULL);
            si.dwFlags |= STARTF_USESTDHANDLES;
            si.hStdInput = (in && in != INVALID_HANDLE_VALUE && GetFileType(in) != FILE_TYPE_UNKNOWN) ? in : nul_in;
            si.hStdOutput = (out && out != INVALID_HANDLE_VALUE) ? out : nul_out;
            si.hStdError = (err && err != INVALID_HANDLE_VALUE) ? err : nul_out;
        }
    }
    if (!CreateProcessW(python, cmdline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        MessageBoxW(NULL, L"GBForge を起動できませんでした。", L"GBForge", MB_ICONERROR);
        return 1;
    }
#ifdef GBF_GUI
    /* ウィンドウの方は起動したら終わってよい (アプリは pythonw.exe で動き続ける) */
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
#endif
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}
