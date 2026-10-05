import os

from src.Helpers.AutonaOutputRetention import PruneOutputFiles


def _make(path, mtime):
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


def test_keeps_newest_generations_and_spares_non_matching(tmp_path):
    for i in range(5):
        _make(tmp_path / f"g{i}.png", 1000 + i)
        _make(tmp_path / f"g{i}.jpg", 1000 + i)
    _make(tmp_path / "notes.txt", 1)
    sub = tmp_path / "sub"
    sub.mkdir()
    _make(sub / "old.png", 1)

    PruneOutputFiles(tmp_path, ("*.png", "*.jpg"), 2)

    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["g3.jpg", "g3.png", "g4.jpg", "g4.png", "notes.txt", "sub"]
    assert (sub / "old.png").exists()


def test_prefix_pattern_leaves_other_files(tmp_path):
    for i in range(4):
        _make(tmp_path / f"thumbnail_{i}.jpg", 1000 + i)
    _make(tmp_path / "other.jpg", 1)

    PruneOutputFiles(tmp_path, ("thumbnail_*.jpg",), 1)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "other.jpg", "thumbnail_3.jpg"]


def test_protect_survives_even_if_oldest(tmp_path):
    _make(tmp_path / "new.png", 1)
    _make(tmp_path / "a.png", 100)
    _make(tmp_path / "b.png", 200)

    PruneOutputFiles(tmp_path, ("*.png",), 1, protect=tmp_path / "new.png")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.png", "new.png"]


def test_missing_directory_is_ignored(tmp_path):
    PruneOutputFiles(tmp_path / "nope", ("*.png",), 1)
