from pathlib import Path
from loguru import logger


def PruneOutputFiles(directory, patterns, keep, protect=None):
    """Delete all but the newest `keep` generations of generated files.

    Only files directly inside `directory` that match one of the glob
    `patterns` are considered; subdirectories and other files are never
    touched. Files sharing a stem (e.g. X.png + X.jpg) form one generation.
    `protect` (optional path) is never deleted, so a file that was just
    written survives even if mtimes tie. Deletion failures (file open in
    another process, etc.) are logged as warnings and ignored.
    """
    try:
        base = Path(directory)
        if not base.is_dir():
            return
        generations = {}
        for pattern in patterns:
            for f in base.glob(pattern):
                if not f.is_file():
                    continue
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                entry = generations.setdefault(f.stem, [0.0, []])
                entry[0] = max(entry[0], mtime)
                entry[1].append(f)

        protectedStem = Path(protect).stem if protect else None
        ordered = sorted(generations.items(),
                         key=lambda kv: (kv[1][0], kv[0]), reverse=True)
        for stem, (_, files) in ordered[max(keep, 0):]:
            if stem == protectedStem:
                continue
            for f in files:
                try:
                    f.unlink()
                except OSError as e:
                    logger.warning(f"Could not delete old output {f}: {e}")
    except Exception as e:
        logger.warning(f"Output pruning failed for {directory}: {e}")
