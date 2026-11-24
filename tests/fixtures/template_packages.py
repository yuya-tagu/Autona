"""The shipped template packages (ADR-007 T1), for a test's own layout/.

The tournament result's layouts and the match thumbnail's templates are the
layout catalog's template packages (src/core/LayoutCatalog.py): a folder under
layout/ whose settings.json carries an ``autona`` manifest. A test whose
working directory has a layout/ of its own puts the shipped manifests there
(Install, or Files for a dict of files), each with a stand-in entry page and,
for a thumbnail, a template file, so that the services and the renderers find
the eight ids they always had. The manifests are the shipped files themselves:
what is drawn in the tests is what the shipped folders declare -- but for
their template contract when a test says it (``contract``): a stand-in page
that defines initThumbnail / initResult is contract 1's, whatever contract
the shipped page is on now (ADR-008 D7 R2: the thumbnails are on 2).

Nothing of the application is imported here.
"""
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# kind -> {id: folder}: a frozen copy of LayoutCatalog.BUNDLED_TEMPLATES
# (tests/test_template_packages.py holds the catalog to it). The ids are what
# settings.json and the state have always saved.
BUNDLED = {
    "result": {
        "horizontal": "result",
        "horizontal_16": "result_top16",
        "vertical": "result_vertical",
        "vertical_geki": "result_vertical_geki",
        "team": "result_team",
        "team_split": "result_team_split",
    },
    "thumbnail": {
        "type_a": "thumbnail",
        "type_b": "thumbnail_type_b",
    },
}
RESULT_FOLDERS = tuple(BUNDLED["result"].values())
THUMBNAIL_FOLDERS = tuple(BUNDLED["thumbnail"].values())
FOLDERS = RESULT_FOLDERS + THUMBNAIL_FOLDERS


def Manifest(folder):
    """The shipped folder's ``autona`` block."""
    return json.loads((PROJECT_ROOT / "layout" / folder / "settings.json")
                      .read_text(encoding="utf-8"))["autona"]


def Settings(folder, contract=None):
    """The shipped folder's settings.json as text; with its manifest's
    ``contract`` replaced when `contract` is given."""
    text = (PROJECT_ROOT / "layout" / folder / "settings.json").read_text(
        encoding="utf-8")
    if contract is None:
        return text
    settings = json.loads(text)
    settings["autona"]["contract"] = contract
    return json.dumps(settings, indent=2)


def Files(folders=FOLDERS, page="", templates=None, contract=None):
    """{path relative to the working directory: text} of `folders`: the
    shipped settings.json (its contract `contract` when given), `page` as the
    entry page and, for a thumbnail, its template file (`templates`: {file
    name: text}; the shipped file when it names none)."""
    files = {}
    for folder in folders:
        source = PROJECT_ROOT / "layout" / folder
        manifest = Manifest(folder)
        files[f"layout/{folder}/settings.json"] = Settings(folder, contract)
        files[f"layout/{folder}/{manifest['entry']}"] = page
        name = manifest.get("template")
        if name is not None:
            files[f"layout/{folder}/{name}"] = (templates or {}).get(name) \
                or (source / name).read_text(encoding="utf-8")
    return files


def Install(layoutRoot, folders=FOLDERS, page=None, contract=None):
    """Put `folders` under `layoutRoot`: the shipped settings.json (its
    contract `contract` when given), a thumbnail's shipped template file, and
    `page` as the entry page (None: an empty one, and an entry page already
    there is kept). The folders it made, for a teardown to remove."""
    made = []
    for folder in folders:
        target = Path(layoutRoot) / folder
        if not target.exists():
            made.append(target)
        target.mkdir(parents=True, exist_ok=True)
        source = PROJECT_ROOT / "layout" / folder
        manifest = Manifest(folder)
        (target / "settings.json").write_text(Settings(folder, contract),
                                              encoding="utf-8")
        entry = target / manifest["entry"]
        if page is not None or not entry.exists():
            entry.write_text(page or "", encoding="utf-8")
        name = manifest.get("template")
        if name is not None:
            shutil.copy(source / name, target / name)
    return made
