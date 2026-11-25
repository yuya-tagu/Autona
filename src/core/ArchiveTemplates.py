"""The archive clipper's title templates (ADR-006 A6, D5): how the worker
names a set's clip and writes its description.

Where they live
---------------
``user_data/archive_templates/<id>.json``, one file per template, in
vodsplit's template format (vodsplit templates.load: the keys of ``KEYS``;
the worker fills a missing one from its DEFAULT, and so does a read here).
The id is the server's: ``default`` (the standard template) or
``YYYYMMDD-HHMMSS-xxxx`` (made by a duplicate); a request names an id, never
a path (``_Path``). The standard template (``STANDARD``: vodsplit's DEFAULT)
is listed when it has no file; saving it writes its file, deleting it
removes the file (back to the standard).

Which one the worker uses
-------------------------
vodsplit's select takes the first template whose ``match`` occurs in the
event name, else the first without one, else its DEFAULT (= STANDARD).
``List`` puts them in that order: those with a match, the longest match
first (a more specific name wins: 「雷影杯 2on2」 before 「雷影杯」), then by
id; then those without, ``default`` first, then by id; last the standard one
when it has no file. An entry's ``role`` says what it is for: ``match`` (the
event names its match occurs in, when no earlier one matched), ``shadowed``
(an earlier template has the same match: it is never taken), ``default``
(the first without a match: an event name no match fits) or ``unused``
(another template without a match comes before it). The page's selection
mirrors it (web/src/ArchivePage/archiveTemplates.ts).

The worker is given the stored ones it can take (``match`` and
``default``; the standard one with no file is its own default already), as
``Check`` reads them, in that order (``WorkerTemplates``): ArchiveJobService
writes them into the job's folder (``<job>/templates/``) and passes those
files, ``--template <path>`` repeated, to ``run`` and ``export``. So the
worker reads what Autona lists (a match with spaces around it, stripped
here, would not be the raw file's), and a template deleted or saved while
the worker runs does not change its run.

Saving
------
``Check`` is strict: every key of its type and length; the chapter format
one Python's str.format reads with {time} and {n} (vodsplit's
templates.chapters); overflow and order of vodsplit's choices; an unknown
key is dropped; the standard template has no match (it is the one the event
names no other template fits use). Written whole (temp file + os.replace).
A stored file that does not pass ``Check`` (edited by hand) is left out of
the list and of the worker's templates, with a warning.

Errors are ``TemplateError`` (its code one of ArchiveJobService's: the
service turns it into its ArchiveError). One module lock orders a read of
the folder against a write. Qt-free; nothing is made, read or written at
import (the folder is resolved against the working directory when used).
"""
import copy
import json
import os
import re
import secrets
import string
import tempfile
import threading
import time

from loguru import logger

DIRECTORY = os.path.join(".", "user_data", "archive_templates")
DEFAULT_ID = "default"
ID_PATTERN = re.compile(r"[0-9]{8}-[0-9]{6}-[a-z0-9]{4}")

# vodsplit's template keys, in the order a file is written.
KEYS = ("name", "match", "title", "title_team", "description",
        "chapter_format", "overflow", "round_map", "aliases", "order")
OVERFLOWS = ("drop_characters", "short_round", "truncate")
ORDERS = ("result", "winner_first")

# vodsplit templates.DEFAULT ("title_team" is the format's, which vodsplit
# does not use yet: kept as written).
STANDARD = {
    "name": "標準",
    "match": None,
    "title": "{event} {round} {p1}({c1}) vs {p2}({c2})",
    "title_team": "",
    "description": ("{event} {round}\n{p1}（{c1}） vs {p2}（{c2}）　{score}\n\n"
                    "{chapters}\n\n▼元配信\n{source_url_t}"),
    "chapter_format": "{time} Game {n}",
    "overflow": "drop_characters",
    "round_map": {},
    "aliases": {},
    "order": "result",
}

MAX_NAME = 60
MAX_MATCH = 100
MAX_TITLE = 500
MAX_DESCRIPTION = 10000
MAX_CHAPTER_FORMAT = 200
MAX_ROUNDS = 200
MAX_ROUND = 100
MAX_ALIASES = 500
MAX_ALIAS = 100
# A duplicate past this many stored templates is refused (each is an
# argument of the worker's command line).
MAX_STORED = 100

MATCH = "match"
SHADOWED = "shadowed"
DEFAULT = "default"
UNUSED = "unused"
# The roles the worker can take a template in (WorkerTemplates).
TAKEN = (MATCH, DEFAULT)

# TemplateError codes: ArchiveJobService's (pinned by the tests).
INVALID = "invalid"
NOT_FOUND = "not_found"
SAVE_FAILED = "save_failed"

_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.05
_ID_CHARACTERS = string.ascii_lowercase + string.digits

_lock = threading.Lock()


class TemplateError(Exception):
    """A refusal: `code` (INVALID / NOT_FOUND / SAVE_FAILED) and the
    Japanese `message` the page shows as it is."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def Standard():
    return copy.deepcopy(STANDARD)


def IsId(value):
    return isinstance(value, str) and (
        value == DEFAULT_ID or bool(ID_PATTERN.fullmatch(value)))


def _Path(templateId):
    return os.path.abspath(os.path.join(DIRECTORY, f"{templateId}.json"))


def _HasControl(text, newlines=False):
    allowed = "\n\t" if newlines else ""
    return any((ord(c) < 32 and c not in allowed) or ord(c) == 127
               for c in text)


def _Line(body, key, label, maximum, required=False):
    value = body.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise TemplateError(INVALID, f"{label}は文字列にしてください。")
    if required and not value.strip():
        raise TemplateError(INVALID, f"{label}を入力してください。")
    if len(value) > maximum:
        raise TemplateError(INVALID, f"{label}は {maximum} 文字以内にしてください。")
    if _HasControl(value):
        raise TemplateError(INVALID, f"{label}に改行や制御文字は使えません。")
    return value


def _Table(value, label, maximum):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TemplateError(INVALID, f"{label}の形式が違います。")
    if len(value) > maximum:
        raise TemplateError(INVALID, f"{label}は {maximum} 件までです。")
    return value


def _Key(key, label):
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_ROUND \
            or _HasControl(key):
        raise TemplateError(INVALID, (
            f"{label}の元の名前は {MAX_ROUND} 文字以内の空でない文字列にしてください。"))
    return key


def _RoundMap(value):
    rounds = {}
    for key, names in _Table(value, "ラウンド名の表記", MAX_ROUNDS).items():
        key = _Key(key, "ラウンド名の表記")
        if not isinstance(names, dict):
            raise TemplateError(INVALID, f"ラウンド「{key}」の表記の形式が違います。")
        entry = {}
        for field in ("ja", "short"):
            text = names.get(field)
            if text is None or text == "":
                continue
            if not isinstance(text, str) or len(text) > MAX_ROUND \
                    or _HasControl(text):
                raise TemplateError(INVALID, (
                    f"ラウンド「{key}」の表記は {MAX_ROUND} 文字以内の文字列にしてください。"))
            entry[field] = text
        if entry:
            rounds[key] = entry
    return rounds


def _Aliases(value):
    aliases = {}
    for key, name in _Table(value, "名前の置き換え", MAX_ALIASES).items():
        key = _Key(key, "名前の置き換え")
        if not isinstance(name, str) or not name.strip() \
                or len(name) > MAX_ALIAS or _HasControl(name):
            raise TemplateError(INVALID, (
                f"「{key}」の置き換え後の名前は {MAX_ALIAS} 文字以内の空でない文字列にしてください。"))
        aliases[key] = name
    return aliases


def Check(body, templateId=None):
    """`body` as a template file holds it (every key of ``KEYS``, in that
    order; unknown keys dropped). `templateId` DEFAULT_ID: the standard
    one, which takes no match. Raises TemplateError(INVALID)."""
    if not isinstance(body, dict):
        raise TemplateError(INVALID, "テンプレートの形式が違います。")
    name = _Line(body, "name", "テンプレートの名前", MAX_NAME,
                 required=True).strip()
    match = body.get("match")
    if match is not None:
        match = _Line(body, "match", "使う大会名", MAX_MATCH).strip() or None
    if match is not None and templateId == DEFAULT_ID:
        raise TemplateError(INVALID, (
            "標準のテンプレートには使う大会名を設定できません。"
            "複製したテンプレートに設定してください。"))
    title = _Line(body, "title", "タイトル", MAX_TITLE, required=True)
    titleTeam = _Line(body, "title_team", "チーム戦のタイトル", MAX_TITLE)
    description = body.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise TemplateError(INVALID, "概要欄は文字列にしてください。")
    description = description.replace("\r\n", "\n")
    if len(description) > MAX_DESCRIPTION:
        raise TemplateError(INVALID, (
            f"概要欄は {MAX_DESCRIPTION} 文字以内にしてください。"))
    if _HasControl(description, newlines=True):
        raise TemplateError(INVALID, "概要欄に制御文字は使えません。")
    chapter = _Line(body, "chapter_format", "チャプターの書式",
                    MAX_CHAPTER_FORMAT, required=True)
    try:
        # As vodsplit's templates.chapters calls it. Whatever it raises
        # ("{n[0]}" is a TypeError, "{time.x}" an AttributeError) is a
        # refusal: the worker would raise it at the end of its run.
        chapter.format(time="0:00", n=1)
    except Exception:
        raise TemplateError(INVALID, (
            "チャプターの書式に使える変数は {time} と {n} だけです"
            "（{ と } をそのまま書くときは {{ }} にしてください）。")) from None
    overflow = body.get("overflow", STANDARD["overflow"])
    if overflow not in OVERFLOWS:
        raise TemplateError(INVALID, "長すぎるタイトルの縮め方を選んでください。")
    order = body.get("order", STANDARD["order"])
    if order not in ORDERS:
        raise TemplateError(INVALID, "選手の並び順を選んでください。")
    return {"name": name, "match": match, "title": title,
            "title_team": titleTeam, "description": description,
            "chapter_format": chapter, "overflow": overflow,
            "round_map": _RoundMap(body.get("round_map")),
            "aliases": _Aliases(body.get("aliases")), "order": order}


# ── the files ────────────────────────────────────────────────────────────

def _Load(path, templateId):
    """The stored template at `path` as Check reads it (the keys a file
    leaves out are the standard's, as vodsplit reads it), or None."""
    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError:
        return None
    except Exception as error:
        # Not only OSError / ValueError: a file nested too deep is a
        # RecursionError. A file edited by hand never breaks the list.
        logger.warning(f"Archive templates: {templateId} could not be read: "
                       f"{error!r}")
        return None
    if not isinstance(raw, dict):
        logger.warning(f"Archive templates: {templateId} is not an object; "
                       f"left out")
        return None
    try:
        return Check({**STANDARD, **raw}, templateId)
    except TemplateError as error:
        logger.warning(f"Archive templates: {templateId} does not pass the "
                       f"checks ({error.message}); left out")
    except Exception as error:
        logger.warning(f"Archive templates: {templateId} could not be "
                       f"checked ({error!r}); left out")
    return None


def _ErrorText(error):
    # An OSError's text names the file (a path of this PC, with the user's
    # name); its reason alone does not. Other errors keep their message
    # (a JSON error names no path and its position is what to investigate).
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return str(error)


def _Stored():
    """{id: template} of the stored files that pass Check (under _lock)."""
    folder = os.path.abspath(DIRECTORY)
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return {}
    except OSError as error:
        logger.warning(f"Archive templates: the folder could not be read: "
                       f"{_ErrorText(error)}")
        return {}
    found = {}
    for name in names:
        stem, suffix = os.path.splitext(name)
        if suffix != ".json" or not IsId(stem):
            continue
        template = _Load(os.path.join(folder, name), stem)
        if template is not None:
            found[stem] = template
    return found


def _Entries(stored):
    """List's entries of `stored` (its order and roles: the module
    docstring)."""
    matched = sorted((key for key, template in stored.items()
                      if template["match"]),
                     key=lambda key: (-len(stored[key]["match"]), key))
    plain = sorted((key for key, template in stored.items()
                    if not template["match"]),
                   key=lambda key: (key != DEFAULT_ID, key))
    # Of templates with one match, the first is taken: vodsplit's select
    # never reaches the others.
    seen = set()
    entries = []
    for key in matched:
        match = stored[key]["match"]
        entries.append({"id": key, "stored": True,
                        "role": SHADOWED if match in seen else MATCH,
                        "template": stored[key]})
        seen.add(match)
    for key in plain:
        entries.append({"id": key, "stored": True,
                        "role": UNUSED if len(entries) > len(matched)
                        else DEFAULT, "template": stored[key]})
    if DEFAULT_ID not in stored:
        entries.append({"id": DEFAULT_ID, "stored": False,
                        "role": UNUSED if len(entries) > len(matched)
                        else DEFAULT, "template": Standard()})
    return entries


def _Write(path, content):
    """Write `content` as JSON to `path` whole (temp file + os.replace).
    Raises OSError."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    data = json.dumps(content, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="template.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(data)
        delay = _REPLACE_BACKOFF
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except OSError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _Unwritable(error):
    logger.warning(f"Archive templates: a file could not be written: "
                   f"{_ErrorText(error)}")
    return TemplateError(SAVE_FAILED, (
        f"テンプレートを保存できません（user_data に書けません）: {error}"))


def _Entry(stored, templateId):
    entry = next((item for item in _Entries(stored)
                  if item["id"] == templateId), None)
    if entry is None:
        raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
    return entry


# ── what the service calls ───────────────────────────────────────────────

def List():
    """Every template, in the worker's order: [{"id", "stored", "role",
    "template"}]."""
    with _lock:
        return _Entries(_Stored())


def Get(templateId):
    """Template `templateId`'s entry. Raises TemplateError(NOT_FOUND)."""
    if not IsId(templateId):
        raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
    with _lock:
        return _Entry(_Stored(), templateId)


def Save(templateId, body):
    """Replace template `templateId` (the standard one, or a stored one)
    with `body` (Check) -> its entry. Raises TemplateError."""
    if not IsId(templateId):
        raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
    template = Check(body, templateId)
    with _lock:
        stored = _Stored()
        if templateId != DEFAULT_ID and templateId not in stored:
            raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
        try:
            _Write(_Path(templateId), template)
        except OSError as error:
            raise _Unwritable(error) from None
        stored[templateId] = template
        logger.info(f"Archive templates: {templateId} saved")
        return _Entry(stored, templateId)


def Duplicate(templateId):
    """A new stored template with template `templateId`'s content, named
    "<name> のコピー" -> its entry. Raises TemplateError."""
    if not IsId(templateId):
        raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
    with _lock:
        stored = _Stored()
        source = _Entry(stored, templateId)["template"]
        if len(stored) >= MAX_STORED:
            raise TemplateError(INVALID, (
                f"テンプレートは {MAX_STORED} 個までです。使わないものを削除してください。"))
        copied = dict(copy.deepcopy(source),
                      name=f"{source['name']} のコピー"[:MAX_NAME])
        for _attempt in range(20):
            newId = (time.strftime("%Y%m%d-%H%M%S") + "-"
                     + "".join(secrets.choice(_ID_CHARACTERS)
                               for _ in range(4)))
            if newId not in stored and not os.path.exists(_Path(newId)):
                break
        else:
            raise TemplateError(SAVE_FAILED, "テンプレートを複製できません。")
        try:
            _Write(_Path(newId), copied)
        except OSError as error:
            raise _Unwritable(error) from None
        stored[newId] = copied
        logger.info(f"Archive templates: {templateId} duplicated as {newId}")
        return _Entry(stored, newId)


def Delete(templateId):
    """Remove template `templateId`'s file -> the standard template's entry
    when it was ``default`` (back to the standard), else None. Raises
    TemplateError (NOT_FOUND: no such stored template)."""
    if not IsId(templateId):
        raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
    with _lock:
        stored = _Stored()
        path = _Path(templateId)
        if templateId != DEFAULT_ID and templateId not in stored \
                and not os.path.isfile(path):
            raise TemplateError(NOT_FOUND, "そのテンプレートはありません。")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise _Unwritable(error) from None
        stored.pop(templateId, None)
        logger.info(f"Archive templates: {templateId} deleted")
        return _Entry(stored, DEFAULT_ID) if templateId == DEFAULT_ID \
            else None


def WorkerTemplates():
    """The templates the worker is given (ArchiveJobService writes them into
    the job's folder for ``--template``): the stored ones it can take (role
    ``match`` or ``default``; the standard one without a file is vodsplit's
    DEFAULT, which it takes when none is given), as Check reads them, in
    List's order."""
    with _lock:
        return [copy.deepcopy(entry["template"])
                for entry in _Entries(_Stored())
                if entry["stored"] and entry["role"] in TAKEN]
