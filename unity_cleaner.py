#!/usr/bin/env python3
"""Unity Project Cleaner - スキャン対象フォルダを選んでプロジェクトを整理するツール"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import box
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.text import Text

console = Console()

DEFAULT_SCAN_DIRS = [
    str(Path.home()),
]

# スキャン時にスキップするフォルダ名
SKIP_DIRS = {
    # Unity キャッシュ
    "Library", "Temp", "obj",
    # 開発系
    "node_modules", ".git", ".svn",
    # macOS システム
    "System", "private", "usr", "bin", "sbin", "etc",
    # macOS ユーザーフォルダの不要場所
    "Applications", "Music", "Movies", "Pictures",
    ".Trash", ".cache", ".npm", ".gradle",
}

# スコアリング用キーワード
TEST_KEYWORDS = ["test", "tmp", "temp", "sample", "tutorial", "練習", "テスト", "試作", "new unity project"]

UNITY_HUB_JSON = Path.home() / "Library/Application Support/UnityHub/projectDir.json"
MEMO_FILENAME = ".unity_memo"


def load_notes() -> dict[str, str]:
    return {}


def save_notes(notes: dict[str, str]):
    pass


def load_project_memo(project_path: Path) -> str:
    try:
        return (project_path / MEMO_FILENAME).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_project_memo(project_path: Path, memo: str):
    memo_file = project_path / MEMO_FILENAME
    if memo:
        memo_file.write_text(memo + "\n", encoding="utf-8")
    else:
        memo_file.unlink(missing_ok=True)


def load_hub_paths() -> set[Path]:
    """Unity Hub に登録されているプロジェクトパスを返す"""
    try:
        import json
        data = json.loads(UNITY_HUB_JSON.read_text())
        # フォーマット: {"version": ..., "paths": [...]} または直接リスト
        paths = data.get("paths", data) if isinstance(data, dict) else data
        return {Path(p).resolve() for p in paths}
    except Exception:
        return set()


def find_unity_projects(scan_dirs: list[str]) -> list[dict]:
    projects = []
    seen = set()
    hub_paths = load_hub_paths()
    notes = load_notes()

    for scan_dir in scan_dirs:
        scan_path = Path(scan_dir).expanduser()
        if not scan_path.exists():
            continue
        _walk_for_unity(scan_path, projects, seen, hub_paths)

    for p in projects:
        p["memo"] = load_project_memo(p["path"])

    return sorted(projects, key=lambda p: p["score"], reverse=True)


def _walk_for_unity(root: Path, projects: list, seen: set, hub_paths: set):
    try:
        entries = list(root.iterdir())
    except PermissionError:
        return

    version_file = root / "ProjectSettings" / "ProjectVersion.txt"
    if version_file.exists() and root not in seen:
        seen.add(root)
        projects.append(analyze_project(root, version_file, hub_paths))
        return

    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue
        if entry.name in SKIP_DIRS or entry.name.startswith("."):
            continue
        _walk_for_unity(entry, projects, seen, hub_paths)


def analyze_project(project_path: Path, version_file: Path, hub_paths: set = None) -> dict:
    # Unity バージョン
    unity_version = "不明"
    try:
        for line in version_file.read_text().splitlines():
            if line.startswith("m_EditorVersion:"):
                unity_version = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    # 最終更新日（Library を除くファイルの最新）
    last_modified = _get_last_modified(project_path)

    # サイズ（Library を除く / Library のみ）
    size_bytes = _get_size_excluding_library(project_path)
    library_bytes = _get_dir_size(project_path / "Library")

    # シーンファイル数
    scene_count = len(list(project_path.rglob("Assets/**/*.unity")))

    # git の有無
    has_git = (project_path / ".git").exists()

    # Unity Hub 登録状態
    in_hub = project_path.resolve() in (hub_paths or set())

    # スコア計算
    score = _calc_score(project_path.name, last_modified, size_bytes, scene_count, has_git)

    return {
        "path": project_path,
        "name": project_path.name,
        "unity_version": unity_version,
        "last_modified": last_modified,
        "size_bytes": size_bytes,
        "size_str": _format_size(size_bytes),
        "library_bytes": library_bytes,
        "library_str": _format_size(library_bytes) if library_bytes else "なし",
        "scene_count": scene_count,
        "has_git": has_git,
        "in_hub": in_hub,
        "score": score,
    }


def _get_last_modified(project_path: Path) -> datetime | None:
    latest = None
    skip_dirs = {"Library", "Temp", "obj", ".git"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            try:
                mtime = Path(root, f).stat().st_mtime
                dt = datetime.fromtimestamp(mtime)
                if latest is None or dt > latest:
                    latest = dt
            except Exception:
                pass
    return latest


def _get_size_excluding_library(project_path: Path) -> int:
    skip_dirs = {"Library", "Temp", "obj"}
    total = 0
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            try:
                total += Path(root, f).stat().st_size
            except Exception:
                pass
    return total


def _get_dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += Path(root, f).stat().st_size
            except Exception:
                pass
    return total


def _format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _calc_score(name: str, last_modified: datetime | None, size_bytes: int, scene_count: int, has_git: bool) -> int:
    score = 0
    now = datetime.now()

    if last_modified:
        days = (now - last_modified).days
        if days > 365:
            score += 3
        elif days > 180:
            score += 2
        elif days > 90:
            score += 1

    if size_bytes < 10 * 1024 * 1024:  # 10MB未満
        score += 2

    if scene_count <= 2:
        score += 2

    if not has_git:
        score += 1

    name_lower = name.lower()
    if any(kw in name_lower for kw in TEST_KEYWORDS):
        score += 1

    return score


def score_label(score: int) -> Text:
    if score >= 5:
        return Text("🔴 削除候補", style="bold red")
    elif score >= 3:
        return Text("🟡 要確認", style="bold yellow")
    else:
        return Text("🟢 保持", style="bold green")


def format_date(dt: datetime | None) -> str:
    if dt is None:
        return "不明"
    return dt.strftime("%Y-%m-%d")


SORT_KEYS = {
    "1": ("score",      True,  "削除スコア"),
    "2": ("last_modified", False, "更新日 (古い順)"),
    "3": ("last_modified", True,  "更新日 (新しい順)"),
    "4": ("size_bytes", True,  "サイズ (大きい順)"),
    "5": ("size_bytes", False, "サイズ (小さい順)"),
    "6": ("name",       False, "名前"),
}


def sort_projects(projects: list[dict]) -> list[dict]:
    console.print("\n[bold]ソート順を選択[/bold]")
    for k, (_, _, label) in SORT_KEYS.items():
        console.print(f"  [{k}] {label}")
    choice = Prompt.ask("選択", choices=list(SORT_KEYS.keys()), default="1")
    key, reverse, _ = SORT_KEYS[choice]
    return sorted(projects, key=lambda p: (p[key] is None, p[key]), reverse=reverse)


def show_projects_table(projects: list[dict]):
    # 同名プロジェクトを検出
    name_counts: dict[str, int] = {}
    for p in projects:
        name_counts[p["name"]] = name_counts.get(p["name"], 0) + 1

    table = Table(box=box.ROUNDED, show_lines=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("プロジェクト名", min_width=18)
    table.add_column("Unity", width=10)
    table.add_column("更新日", width=12)
    table.add_column("サイズ", width=8)
    table.add_column("Git", width=3)
    table.add_column("Hub", width=3)
    table.add_column("判定", width=10)
    table.add_column("メモ", min_width=12)

    for i, p in enumerate(projects, 1):
        git_icon = "✓" if p["has_git"] else "✗"
        git_style = "green" if p["has_git"] else "red"
        hub_icon = "✓" if p["in_hub"] else "✗"
        hub_style = "green" if p["in_hub"] else "dim"
        name_text = Text(p["name"])
        if name_counts[p["name"]] > 1:
            name_text = Text(f"⚠ {p['name']}", style="bold orange3")
        # Unity バージョンを短縮 (例: 6000.2.15f1 → 6000.2)
        ver = p["unity_version"]
        ver_short = ".".join(ver.split(".")[:2]) if ver != "不明" else ver
        memo = p.get("memo", "")
        memo_text = Text(memo[:20] + ("…" if len(memo) > 20 else ""), style="italic dim") if memo else Text("", style="dim")
        table.add_row(
            str(i),
            name_text,
            ver_short,
            format_date(p["last_modified"]),
            p["size_str"],
            Text(git_icon, style=git_style),
            Text(hub_icon, style=hub_style),
            score_label(p["score"]),
            memo_text,
        )

    console.print(table)


def show_project_detail(p: dict):
    path = p["path"]

    # シーン一覧
    scenes = list(path.rglob("Assets/**/*.unity"))
    # スクリプト数
    scripts = list(path.rglob("Assets/**/*.cs"))
    # アセット種別ごとの数
    asset_types: dict[str, int] = {}
    for f in path.rglob("Assets/**/*"):
        if f.is_file() and f.suffix not in ("", ".meta"):
            ext = f.suffix.lower()
            asset_types[ext] = asset_types.get(ext, 0) + 1

    top_assets = sorted(asset_types.items(), key=lambda x: x[1], reverse=True)[:10]

    name_counts_str = str(p["path"]).replace(str(Path.home()), "~")
    console.print(Panel(f"[bold cyan]{p['name']}[/bold cyan]\n[dim]{name_counts_str}[/dim]", expand=False))

    # 基本情報
    info = Table(box=box.SIMPLE, show_header=False)
    info.add_column("key", style="bold", width=16)
    info.add_column("value")
    info.add_row("Unity バージョン", p["unity_version"])
    info.add_row("最終更新日", format_date(p["last_modified"]))
    info.add_row("サイズ (実体)", p["size_str"])
    info.add_row("Library (キャッシュ)", p["library_str"])
    info.add_row("Git 管理", "あり ✓" if p["has_git"] else "なし ✗")
    info.add_row("削除スコア", f"{p['score']} 点  " + score_label(p["score"]).plain)
    console.print(info)

    # シーン一覧
    console.print(f"[bold]シーン ({len(scenes)} 個)[/bold]")
    if scenes:
        for s in scenes:
            console.print(f"  • {s.relative_to(path)}")
    else:
        console.print("  [dim]なし[/dim]")

    # スクリプト
    console.print(f"\n[bold]C# スクリプト ({len(scripts)} 個)[/bold]")

    # アセット種別
    if top_assets:
        console.print("\n[bold]アセット種別 (上位10件)[/bold]")
        at = Table(box=box.SIMPLE, show_header=False)
        at.add_column("拡張子", width=12)
        at.add_column("数", justify="right")
        for ext, count in top_assets:
            at.add_row(ext, str(count))
        console.print(at)

    # メモ
    console.print("\n[bold]メモ[/bold]")
    current_memo = p.get("memo", "")
    if current_memo:
        console.print(f"  {current_memo}")
    else:
        console.print("  [dim]なし[/dim]")

    # アクション
    console.print("\n[1] Finder で開く  [2] メモを編集  [3] Library を削除 (キャッシュクリア)  [q] 戻る")
    action = Prompt.ask("選択", default="q")

    if action == "1":
        subprocess.run(["open", str(p["path"])])
        console.print("[green]Finder で開きました[/green]")

    elif action == "2":
        new_memo = Prompt.ask("メモ (空白で削除)", default=current_memo)
        save_project_memo(p["path"], new_memo.strip())
        p["memo"] = new_memo.strip()
        console.print("[green]保存しました[/green]")

    elif action == "3":
        lib = p["path"] / "Library"
        if not lib.exists():
            console.print("[yellow]Library フォルダが存在しません[/yellow]")
        else:
            console.print(f"[bold]Library サイズ: {p['library_str']}[/bold]")
            if Confirm.ask("Library を削除しますか？（Unity で開くと自動再生成されます）", default=False):
                try:
                    shutil.rmtree(lib)
                    p["library_bytes"] = 0
                    p["library_str"] = "なし"
                    console.print("[green]削除しました[/green]")
                except Exception as e:
                    console.print(f"[red]削除失敗: {e}[/red]")


def hub_register_menu(projects: list[dict]):
    import json

    not_in_hub = [p for p in projects if not p["in_hub"]]
    if not not_in_hub:
        console.print("[green]全プロジェクトが既に Hub に登録されています[/green]")
        return

    console.print(f"\n[bold]Hub 未登録のプロジェクト ({len(not_in_hub)} 個)[/bold]")
    for i, p in enumerate(not_in_hub, 1):
        console.print(f"  {i}. {p['name']}  [dim]{str(p['path']).replace(str(Path.home()), '~')}[/dim]")

    console.print("\n[1] 全て登録  [2] 番号を選んで登録  [q] キャンセル")
    choice = Prompt.ask("選択").strip()

    if choice == "1":
        targets = not_in_hub
    elif choice == "2":
        nums = Prompt.ask("番号 (例: 1,3)").strip()
        try:
            targets = [not_in_hub[int(x.strip()) - 1] for x in nums.split(",")]
        except (ValueError, IndexError):
            console.print("[red]無効な番号です[/red]")
            return
    else:
        return

    if not UNITY_HUB_JSON.exists():
        console.print(f"[red]Unity Hub の設定ファイルが見つかりません: {UNITY_HUB_JSON}[/red]")
        return

    try:
        data = json.loads(UNITY_HUB_JSON.read_text())
        paths = data.get("paths", []) if isinstance(data, dict) else data
        for p in targets:
            path_str = str(p["path"].resolve())
            if path_str not in paths:
                paths.append(path_str)
                p["in_hub"] = True
                console.print(f"[green]登録しました: {p['name']}[/green]")
        if isinstance(data, dict):
            data["paths"] = paths
        else:
            data = paths
        UNITY_HUB_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        console.print(f"[red]Hub への登録に失敗しました: {e}[/red]")


def manage_scan_dirs() -> list[str]:
    scan_dirs = [d for d in DEFAULT_SCAN_DIRS if Path(d).exists()]

    while True:
        console.print(Panel("\n".join(f"  {i+1}. {d}" for i, d in enumerate(scan_dirs)), title="[bold]スキャン対象フォルダ", expand=False))
        console.print("[1] フォルダを追加  [2] フォルダを削除  [3] このまま開始\n")
        choice = Prompt.ask("選択", choices=["1", "2", "3"], default="3")

        if choice == "1":
            new_dir = Prompt.ask("追加するフォルダのパス")
            new_dir = str(Path(new_dir).expanduser())
            if Path(new_dir).exists():
                scan_dirs.append(new_dir)
                console.print(f"[green]追加しました: {new_dir}[/green]")
            else:
                console.print(f"[red]フォルダが見つかりません: {new_dir}[/red]")

        elif choice == "2":
            if not scan_dirs:
                console.print("[red]削除できるフォルダがありません[/red]")
                continue
            num = Prompt.ask("削除する番号", default="1")
            try:
                idx = int(num) - 1
                removed = scan_dirs.pop(idx)
                console.print(f"[yellow]削除しました: {removed}[/yellow]")
            except (ValueError, IndexError):
                console.print("[red]無効な番号です[/red]")

        elif choice == "3":
            break

    return scan_dirs


def _move_to_trash(path: Path):
    script = f'tell application "Finder" to delete POSIX file "{path}"'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())


def delete_menu(projects: list[dict]):
    while True:
        console.print("\n[bold]削除するプロジェクトの番号を入力してください[/bold]")
        console.print("[dim]例: 1  /  1,3,5  /  q で戻る[/dim]\n")
        choice = Prompt.ask("番号").strip()

        if choice.lower() == "q":
            break

        try:
            indices = [int(x.strip()) - 1 for x in choice.split(",")]
        except ValueError:
            console.print("[red]無効な入力です[/red]")
            continue

        targets = []
        for idx in indices:
            if 0 <= idx < len(projects):
                targets.append(projects[idx])
            else:
                console.print(f"[red]番号 {idx+1} は存在しません[/red]")

        if not targets:
            continue

        console.print("\n[bold red]以下のプロジェクトを削除します:[/bold red]")
        for p in targets:
            console.print(f"  • {p['name']}  ({p['path']})")

        if Confirm.ask("\nゴミ箱に移動しますか？", default=False):
            for p in targets:
                try:
                    _move_to_trash(p["path"])
                    console.print(f"[green]ゴミ箱に移動しました: {p['name']}[/green]")
                    projects.remove(p)
                except Exception as e:
                    console.print(f"[red]失敗 {p['name']}: {e}[/red]")
        else:
            console.print("[yellow]キャンセルしました[/yellow]")


def main():
    console.print(Panel("[bold cyan]Unity Project Cleaner[/bold cyan]\nプロジェクトを整理して不要なものを削除します", expand=False))

    # スキャン対象フォルダ設定
    scan_dirs = manage_scan_dirs()
    if not scan_dirs:
        console.print("[red]スキャン対象フォルダがありません[/red]")
        return

    # スキャン
    console.print("[bold green]Unityプロジェクトをスキャン中... (時間がかかる場合があります)[/bold green]")
    projects = find_unity_projects(scan_dirs)

    if not projects:
        console.print("[yellow]Unityプロジェクトが見つかりませんでした[/yellow]")
        return

    console.print(f"\n[bold]{len(projects)} 個のプロジェクトが見つかりました[/bold]\n")
    show_projects_table(projects)

    # メインメニュー
    while True:
        console.print("\n[1] 詳細を見る  [2] ソート  [3] Hub に登録  [4] ゴミ箱に移動  [5] 終了")
        choice = Prompt.ask("選択", choices=["1", "2", "3", "4", "5"], default="5")

        if choice == "1":
            num = Prompt.ask("番号を入力").strip()
            try:
                idx = int(num) - 1
                if 0 <= idx < len(projects):
                    show_project_detail(projects[idx])
                else:
                    console.print("[red]無効な番号です[/red]")
            except ValueError:
                console.print("[red]数字を入力してください[/red]")

        elif choice == "2":
            projects = sort_projects(projects)
            show_projects_table(projects)

        elif choice == "3":
            hub_register_menu(projects)

        elif choice == "4":
            delete_menu(projects)
            show_projects_table(projects)

        elif choice == "5":
            break

    console.print("\n[bold green]終了しました[/bold green]")


if __name__ == "__main__":
    main()
