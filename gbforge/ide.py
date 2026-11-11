"""Visual Studio / VS Code 連携 (Unity の「スクリプトを開くと IDE が起動する」に相当)。

プロジェクトに次のファイルを生成する:
  VS Code        : .vscode/c_cpp_properties.json, tasks.json, settings.json, extensions.json
  Visual Studio  : <名前>.sln, <名前>.vcxproj(.filters)   (Makefile プロジェクト)
                   CppProperties.json                     (「フォルダーを開く」用)

インクルードパスには GBDK-2020 の include と gbjp (日本語フォントライブラリ) が入るので、
IntelliSense (補完・定義へ移動) がそのまま効く。ビルド・実行 (F5)・書き込みは gbforge を呼び出す。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from .builder import ENGINE_DIR, find_gbdk
from .project import Project

# SDCC 独自のキーワードを IntelliSense 用に無害化する
SDCC_DEFINES = [
    "__PORT_sm83", "__TARGET_gb", "__SDCC", "__SDCC_sm83", "__INTELLISENSE_GBDK__",
    "__banked=", "__nonbanked=", "__at(x)=", "__sfr=volatile unsigned char", "__critical=",
    "__interrupt(x)=", "__naked=", "__reentrant=", "__preserves_regs(...)=", "__sdcccall(x)=",
    "__z88dk_fastcall=", "__z88dk_callee=", "__wparam=", "__trap=", "__using(x)=", "__data=", "__code=",
    "__xdata=", "__near=", "__far=", "__smallc=", "__bit=char", "__sbit=char", "__asm=", "__endasm=",
    "__nonreentrant=", "__shadowregs=", "__raisonance=", "__iar=", "__cosmic=", "__builtin_offsetof(t,m)=0",
]


def gbforge_command() -> list[str]:
    from .actions import self_command
    return self_command()


def include_dirs(p: Project) -> list[Path]:
    dirs: list[Path] = []
    lcc = find_gbdk(p)
    if lcc:
        dirs.append(lcc.parent.parent / "include")
    if p.is_c:
        dirs += [p.src_dir, p.build_dir / "gen"]
        if p.lib_dir.exists():
            dirs += sorted({f.parent for f in p.lib_dir.rglob("*.h")})
    else:
        dirs += [ENGINE_DIR, p.build_dir / "gen", p.scripts_dir]
    return dirs


def _code_files(p: Project) -> list[Path]:
    if p.is_c:
        files = [p.root / f for f in p.source_files()]
    else:
        files = sorted(p.scripts_dir.glob("*.c"))
    return files


def _rel(p: Project, f: Path) -> str:
    try:
        return f.relative_to(p.root).as_posix()
    except ValueError:
        return f.as_posix()


# ---------------------------------------------------------------------------
# VS Code
# ---------------------------------------------------------------------------
def _vscode(p: Project, incs: list[Path], cmd: list[str]):
    d = p.root / ".vscode"
    d.mkdir(exist_ok=True)
    inc_strs = [("${workspaceFolder}/" + _rel(p, i)) if p.root in i.parents or i == p.root else i.as_posix()
                for i in incs]
    (d / "c_cpp_properties.json").write_text(json.dumps({
        "configurations": [{
            "name": "GBDK (GBForge)",
            "includePath": inc_strs,
            "defines": SDCC_DEFINES,
            "compilerPath": "",
            "cStandard": "c11",
            "intelliSenseMode": "gcc-x86",
        }],
        "version": 4,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    def task(label, args, group=None, default=False):
        t = {"label": label, "type": "process", "command": cmd[0], "args": cmd[1:] + args,
             "options": {"cwd": "${workspaceFolder}"},
             "problemMatcher": {
                 "owner": "gbdk", "fileLocation": ["autoDetect", "${workspaceFolder}"],
                 "pattern": {"regexp": r"^(.*?):(\d+):\s*(error|warning)\s*\d*:?\s*(.*)$",
                             "file": 1, "line": 2, "severity": 3, "message": 4}},
             "presentation": {"reveal": "always", "panel": "shared", "clear": True}}
        if group:
            t["group"] = {"kind": group, "isDefault": True} if default else group
        return t

    rom = _rel(p, p.rom_path)
    (d / "tasks.json").write_text(json.dumps({"version": "2.0.0", "tasks": [
        task("GBForge: ビルド", ["build", "."], "build", True),
        task("GBForge: 実行 (エミュレータ)", ["run", "."], "test", True),
        task("GBForge: カートに書き込み", ["flash", "."]),
        task("GBForge: エディタを開く", ["editor", "."]),
        task("GBForge: ROM だけ遊ぶ", ["play", rom]),
    ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    settings_f = d / "settings.json"
    settings = {}
    if settings_f.exists():
        try:
            settings = json.loads(settings_f.read_text(encoding="utf-8"))
        except ValueError:
            settings = {}
    settings.update({"files.encoding": "utf8", "files.associations": {"*.h": "c", "*.gbproj": "json"},
                     "C_Cpp.default.cStandard": "c11"})
    settings_f.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "extensions.json").write_text(json.dumps({"recommendations": ["ms-vscode.cpptools"]}, indent=2),
                                       encoding="utf-8")


# ---------------------------------------------------------------------------
# Visual Studio (Makefile プロジェクト)
# ---------------------------------------------------------------------------
def _win(path: Path | str) -> str:
    return str(path).replace("/", "\\")


def _q(s: str) -> str:
    return f'"{s}"' if " " in s or "\\" in s or ":" in s else s


def _vs(p: Project, incs: list[Path], cmd: list[str]):
    name = p.safe_filename()
    proj_guid = str(uuid.uuid5(uuid.NAMESPACE_URL, "gbforge:" + str(p.root))).upper()
    sln_guid = str(uuid.uuid5(uuid.NAMESPACE_URL, "gbforge-sln:" + str(p.root))).upper()
    gb = " ".join(_q(_win(c)) for c in cmd)
    inc = ";".join(_win(i) for i in incs)
    defs = ";".join(SDCC_DEFINES)
    rom = _win(p.rom_path.relative_to(p.root))
    files = _code_files(p)
    extra = [p.root / "project.gbproj"]
    if p.is_c:
        extra += sorted(p.res_dir.rglob("*.png")) if p.res_dir.exists() else []
    else:
        extra += [p.texts_file] + sorted(p.scenes_dir.glob("*.json"))

    def item(f: Path) -> tuple[str, str]:
        rel = _win(f.relative_to(p.root)) if p.root in f.parents else _win(f)
        kind = "ClCompile" if f.suffix == ".c" else "ClInclude" if f.suffix == ".h" else "None"
        return kind, rel

    items = [item(f) for f in files + [e for e in extra if e.exists()]]
    if not p.is_c:   # エンジンの API ヘッダーも見えるように
        items.append(("ClInclude", _win(ENGINE_DIR / "gbforge.h")))

    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<!-- 自動生成: GBForge (gbforge ide で再生成されます) -->
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemGroup Label="ProjectConfigurations">
    <ProjectConfiguration Include="Debug|Win32">
      <Configuration>Debug</Configuration>
      <Platform>Win32</Platform>
    </ProjectConfiguration>
  </ItemGroup>
  <PropertyGroup Label="Globals">
    <VCProjectVersion>17.0</VCProjectVersion>
    <ProjectGuid>{{{proj_guid}}}</ProjectGuid>
    <Keyword>MakeFileProj</Keyword>
    <RootNamespace>{escape(name)}</RootNamespace>
  </PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />
  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='Debug|Win32'" Label="Configuration">
    <ConfigurationType>Makefile</ConfigurationType>
    <PlatformToolset>v143</PlatformToolset>
  </PropertyGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.props" />
  <PropertyGroup Condition="'$(Configuration)|$(Platform)'=='Debug|Win32'">
    <NMakeBuildCommandLine>{escape(gb)} build "$(ProjectDir)."</NMakeBuildCommandLine>
    <NMakeReBuildCommandLine>{escape(gb)} build --clean "$(ProjectDir)."</NMakeReBuildCommandLine>
    <NMakeCleanCommandLine>{escape(gb)} clean "$(ProjectDir)."</NMakeCleanCommandLine>
    <NMakeOutput>$(ProjectDir){escape(rom)}</NMakeOutput>
    <NMakeIncludeSearchPath>{escape(inc)}</NMakeIncludeSearchPath>
    <NMakePreprocessorDefinitions>{escape(defs)}</NMakePreprocessorDefinitions>
    <AdditionalOptions>/std:c11</AdditionalOptions>
    <OutDir>$(ProjectDir)build\\</OutDir>
    <IntDir>$(ProjectDir)build\\vs\\</IntDir>
    <LocalDebuggerCommand>{escape(_win(cmd[0]))}</LocalDebuggerCommand>
    <LocalDebuggerCommandArguments>{escape(" ".join(_q(c) for c in cmd[1:]))} play "$(ProjectDir){escape(rom)}"</LocalDebuggerCommandArguments>
    <LocalDebuggerWorkingDirectory>$(ProjectDir)</LocalDebuggerWorkingDirectory>
    <DebuggerFlavor>WindowsLocalDebugger</DebuggerFlavor>
  </PropertyGroup>
  <ItemGroup>
'''
    for kind, rel in items:
        xml += f'    <{kind} Include="{escape(rel)}" />\n'
    xml += '''  </ItemGroup>
  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />
</Project>
'''
    (p.root / f"{name}.vcxproj").write_text(xml, encoding="utf-8-sig")

    # フィルター (ソリューションエクスプローラーのフォルダ表示)
    folders = sorted({str(Path(rel).parent) for _, rel in items if not Path(rel).is_absolute()} - {"."})
    flt = ['<?xml version="1.0" encoding="utf-8"?>',
           '<Project ToolsVersion="4.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">', "  <ItemGroup>"]
    for f in folders:
        fg = str(uuid.uuid5(uuid.NAMESPACE_URL, "gbforge-filter:" + f)).upper()
        flt.append(f'    <Filter Include="{escape(f)}"><UniqueIdentifier>{{{fg}}}</UniqueIdentifier></Filter>')
    flt.append("  </ItemGroup>\n  <ItemGroup>")
    for kind, rel in items:
        parent = str(Path(rel).parent)
        if parent in folders:
            flt.append(f'    <{kind} Include="{escape(rel)}"><Filter>{escape(parent)}</Filter></{kind}>')
        else:
            flt.append(f'    <{kind} Include="{escape(rel)}" />')
    flt.append("  </ItemGroup>\n</Project>\n")
    (p.root / f"{name}.vcxproj.filters").write_text("\n".join(flt), encoding="utf-8-sig")

    sln = f'''
Microsoft Visual Studio Solution File, Format Version 12.00
# Visual Studio Version 17
VisualStudioVersion = 17.0.31903.59
MinimumVisualStudioVersion = 10.0.40219.1
Project("{{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}}") = "{name}", "{name}.vcxproj", "{{{proj_guid}}}"
EndProject
Global
\tGlobalSection(SolutionConfigurationPlatforms) = preSolution
\t\tDebug|x86 = Debug|x86
\tEndGlobalSection
\tGlobalSection(ProjectConfigurationPlatforms) = postSolution
\t\t{{{proj_guid}}}.Debug|x86.ActiveCfg = Debug|Win32
\t\t{{{proj_guid}}}.Debug|x86.Build.0 = Debug|Win32
\tEndGlobalSection
\tGlobalSection(ExtensibilityGlobals) = postSolution
\t\tSolutionGuid = {{{sln_guid}}}
\tEndGlobalSection
EndGlobal
'''
    (p.root / f"{name}.sln").write_text(sln.replace("\n", "\r\n"), encoding="utf-8-sig")

    # Visual Studio の「フォルダーを開く」用
    (p.root / "CppProperties.json").write_text(json.dumps({"configurations": [{
        "name": "GBDK (GBForge)", "inheritEnvironments": ["msvc_x86"], "intelliSenseMode": "windows-msvc-x86",
        "includePath": [i.as_posix() for i in incs], "defines": SDCC_DEFINES,
    }]}, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_ide_files(p: Project) -> list[Path]:
    incs = include_dirs(p)
    cmd = gbforge_command()
    _vscode(p, incs, cmd)
    _vs(p, incs, cmd)
    name = p.safe_filename()
    return [p.root / ".vscode", p.root / f"{name}.sln", p.root / f"{name}.vcxproj"]


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------
def find_vscode() -> str | None:
    c = shutil.which("code") or shutil.which("code.cmd")
    if c:
        return c
    cands = []
    if platform.system() == "Darwin":
        cands.append("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        cands += [os.path.join(la, "Programs", "Microsoft VS Code", "bin", "code.cmd"),
                  r"C:\Program Files\Microsoft VS Code\bin\code.cmd"]
    return next((c for c in cands if os.path.exists(c)), None)


def find_visual_studio() -> str | None:
    if os.name != "nt":
        return None
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.exists():
        try:
            out = subprocess.run([str(vswhere), "-latest", "-property", "productPath"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def open_in_ide(p: Project, which: str | None = None, file: str | None = None, line: int | None = None) -> str:
    """IDE でプロジェクト (とファイル) を開く。使った IDE の名前を返す。"""
    generate_ide_files(p)
    which = which or p.settings.get("ide", {}).get("editor", "auto")
    target = p.resolve(file) if file else None
    vs = find_visual_studio()
    code = find_vscode()
    if which == "auto":
        which = "visualstudio" if vs else "vscode" if code else ""
    if which == "visualstudio":
        if not vs:
            raise RuntimeError("Visual Studio が見つかりません (Windows に Visual Studio 2022 をインストールしてください)")
        sln = p.root / f"{p.safe_filename()}.sln"
        args = [vs, str(sln)]
        if target:
            args += ["/Edit", str(target)]
        subprocess.Popen(args, cwd=str(p.root))
        return "Visual Studio"
    if which == "vscode":
        if not code:
            raise RuntimeError("VS Code が見つかりません (インストールして PATH に code を追加してください)")
        args = [code, str(p.root)]
        if target:
            args += ["-g", f"{target}:{line or 1}"]
        subprocess.Popen(args, cwd=str(p.root), shell=(os.name == "nt" and code.endswith(".cmd")))
        return "VS Code"
    raise RuntimeError("Visual Studio も VS Code も見つかりません。どちらかをインストールしてください")
