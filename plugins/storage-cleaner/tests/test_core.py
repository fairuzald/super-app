"""Unit tests for cleaner_core's cross-platform helpers. Run on Linux, Windows (MSYS2) and macOS in CI.

They use a fake home (SC_HOME) and a fake Trash (SC_TRASH), so nothing real is touched.
"""
import importlib
import os
import sys
import time

import pytest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")


@pytest.fixture
def core(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Downloads").mkdir()
    trash = tmp_path / "trash"
    trash.mkdir()
    monkeypatch.setenv("SC_HOME", str(home))
    monkeypatch.setenv("SC_TRASH", str(trash))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    sys.path.insert(0, APP)
    sys.modules.pop("cleaner_core", None)
    mod = importlib.import_module("cleaner_core")
    yield mod
    sys.path.remove(APP)
    sys.modules.pop("cleaner_core", None)


def write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_tree_size_counts_files_and_skips_links(core, tmp_path):
    d = tmp_path / "home" / "proj"
    write(d / "a.bin", 300_000)
    write(d / "sub" / "b.bin", 200_000)
    if hasattr(os, "symlink"):
        try:
            os.symlink(tmp_path, d / "loop")          # a link back up must not be followed
        except OSError:
            pass
    size = core.tree_size(str(d))
    assert 500_000 <= size <= 600_000                 # block rounding on Linux/macOS


def test_safety_rules(core, tmp_path):
    home = tmp_path / "home"
    f = write(home / "Documents" / "old.zip", 10)
    assert core.is_safe(str(f))
    assert not core.is_safe(str(home))                                # the home folder itself
    assert not core.is_safe(str(home / "Documents"))                  # protected folders
    assert not core.is_safe(str(tmp_path / "elsewhere.txt"))          # outside home
    assert not core.is_safe(str(write(home / ".ssh" / "id_ed25519", 1)))   # secrets
    assert not core.is_safe(str(home / "Documents" / ".." / ".." / "x"))   # no climbing out
    assert not core.is_safe(str(write(home / "AppData" / "Roaming" / "Microsoft" / "Credentials" / "c", 1)))
    trashed = write(tmp_path / "trash" / "gone.txt", 1)
    assert core.is_safe(str(trashed), allow_trash=True)
    assert not core.is_safe(str(trashed))                              # Trash only when emptying
    assert not core.is_safe(str(f), allow_trash=True)


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows paths are case-insensitive")
def test_windows_safety_is_case_insensitive(core, tmp_path):
    p = str(tmp_path / "home" / "DOCUMENTS")
    assert not core.is_safe(p)


def test_symlink_to_secret_is_removed_not_followed(core, tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks")
    home = tmp_path / "home"
    secret = write(home / ".ssh" / "key", 5)
    link = home / "Documents" / "link"
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("symlinks need privileges here")
    core.remove_path(str(link), mode="delete")
    assert not os.path.lexists(link) and secret.exists()


def test_recycle_bin_info_v2(core, tmp_path):
    orig = "C:\\Users\\me\\Downloads\\setup.exe"
    ft = int((time.time() - 3600) * 1e7) + 116444736000000000
    data = (2).to_bytes(8, "little") + (1234).to_bytes(8, "little") + ft.to_bytes(8, "little")
    data += (len(orig) + 1).to_bytes(4, "little") + (orig + "\0").encode("utf-16-le")
    info = tmp_path / "$IABC123.exe"
    info.write_bytes(data)
    path, when = core.read_recycle_info(str(info))
    assert path == orig and abs(when - (time.time() - 3600)) < 5


def test_large_files_and_folder_sizes_walkers(core, tmp_path):
    home = tmp_path / "home"
    big = write(home / "Videos" / "film.mkv", 3_000_000)
    write(home / "proj" / "node_modules" / "huge.bin", 3_000_000)          # skipped by the large-file scan
    found = core.large_files_py(str(home), 2_000_000)
    assert [p for _s, _m, p in found] == [str(big)]
    sizes = core.folder_sizes_py(str(home), 1_000_000)
    assert sizes[str(home)] >= 6_000_000 and str(home / "Videos") in sizes


def test_parent_walk_stops_at_drive_root(core):
    root = os.path.abspath(os.sep)
    assert core.git_root(os.path.join(root, "nope")) is None             # must terminate, also on C:\\


def test_categories_for_this_os(core):
    ids = {C.id for C in core.CATEGORIES}
    assert {"caches", "devcaches", "trash", "node", "build", "docker", "downloads", "large"} <= ids
    assert ("system" in ids) == core.IS_LINUX


def test_scans_run_on_an_empty_home(core):
    for C in core.CATEGORIES:
        if C.id in ("system", "docker"):
            continue
        cat = C()
        cat.refresh()
        assert cat.error is None, (C.id, cat.error)


def test_best_effort_item_skips_failures(core, tmp_path):
    home = tmp_path / "home"
    ok = write(home / "tmpstuff" / "a.tmp", 10)
    it = core.Item("Temporary files", 10, [str(ok), str(home / "Documents")], best_effort=True)
    res = core.clean_items([it], mode="delete")
    assert res["errors"] == [] and not ok.exists() and (home / "Documents").exists()
