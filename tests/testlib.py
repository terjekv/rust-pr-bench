import importlib.util
import pathlib
from types import ModuleType


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script_module(name: str, relative_path: str) -> ModuleType:
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_snapshot(name: str) -> str:
    return (REPO_ROOT / "tests" / "snapshots" / name).read_text(encoding="utf-8")
