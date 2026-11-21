"""タブに開く画面。import すると main.VIEWS に登録される。"""
from importlib import import_module

for _m in ("code", "texts", "assets", "settings", "convert", "carray", "versions", "scene", "stage", "media"):
    try:
        import_module(f"{__name__}.{_m}")
    except ModuleNotFoundError as e:   # まだ無い画面 (作成途中)
        if e.name != f"{__name__}.{_m}":
            raise
