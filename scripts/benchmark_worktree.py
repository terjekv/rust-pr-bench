"""Isolated worktrees, with stable source paths for opt-in executable reuse."""

from contextlib import contextmanager
import fcntl
import pathlib
import subprocess

from build_cache import cache_root, enabled


@contextmanager
def benchmark_worktree(source: pathlib.Path, work: pathlib.Path, revision: str):
    stable = enabled("CACHE_BINARIES", "false") and enabled("CACHE")
    repository = cache_root(source) / "repository" if stable else work / "repository"
    lock = None

    def git(*args, **kwargs):
        return subprocess.run(["git", *args], cwd=source, check=True, **kwargs)

    try:
        if stable:
            repository.parent.mkdir(parents=True, exist_ok=True)
            lock = (repository.parent / "repository.lock").open("a")
            # A shared namespace on a self-hosted runner must not use the worktree concurrently.
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError(
                    "benchmark cache namespace is already in use on this runner"
                ) from error
            if repository.exists():
                # Recover only a worktree registered by this repository, e.g. after cancellation.
                listing = (
                    subprocess.check_output(
                        ["git", "worktree", "list", "--porcelain", "-z"], cwd=source
                    )
                    .decode()
                    .split("\0")
                )
                if f"worktree {repository}" not in listing or repository.is_symlink():
                    raise ValueError(
                        "stable benchmark worktree path is occupied by an unrelated directory"
                    )
                git("worktree", "remove", "--force", str(repository))
        git("worktree", "add", "--detach", str(repository), revision)
        try:
            yield repository
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(repository)],
                cwd=source,
                check=False,
            )
    finally:
        if lock is not None:
            lock.close()
