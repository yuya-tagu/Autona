"""The archive clipper's thumbnails (ADR-006 A6): what the match thumbnail
(core/ThumbnailService, thumbnail.html) is given to draw a set of a job
instead of the board.

``SetData`` makes, from a set's result, data of the state's shape that
thumbnail.js reads -- ``game.codename``, ``tournamentInfo``, and board 1
(``score.1``: the round as its ``match``, each side's one player with its
name and up to two characters) -- and nothing else. The characters are the
codenames ArchiveResults keeps in the set's ``autona`` (A5), of its game
(not necessarily the loaded one: GameAssetCatalog.GameCharacterAssets), as
a character slot holds one (PlayerService.BuildCharacterValue's keys:
``name``, ``en_name``, ``display_name``, ``codename``, ``assets``, ``skin``,
``variant``; skin 0). A set without them has no characters (the names are
drawn). The state is neither read nor written.

Pure apart from the catalog's reads (the game's folder for a game that is
not loaded); nothing happens at import.
"""

# ArchiveResults.NAMESPACE (not imported: ArchiveResults imports the job
# service, which imports this module). Pinned equal by the tests.
NAMESPACE = "autona"
# How many characters a side has (ArchiveResults.MAX_SHOWN).
MAX_CHARACTERS = 2


def _Characters(game, codenames, catalog, cache):
    """{"1": value, ...} of `codenames` (at most MAX_CHARACTERS) of game
    `game`; the ones the game does not name are left out."""
    if catalog is None or not isinstance(game, str) or not game or \
            not isinstance(codenames, list):
        return {}
    names = cache.get(game)
    if names is None:
        names = cache[game] = {
            character.get("codename"): (en_name, character.get(
                "display_name") or en_name)
            for en_name, character in catalog.GameCharacters(game).items()
            if isinstance(character, dict)}
    characters = {}
    for codename in codenames:
        if len(characters) >= MAX_CHARACTERS:
            break
        if not isinstance(codename, str) or codename not in names:
            continue
        en_name, display_name = names[codename]
        characters[str(len(characters) + 1)] = {
            "name": display_name, "en_name": en_name,
            "display_name": display_name, "codename": codename,
            "assets": catalog.GameCharacterAssets(game, codename, 0),
            "skin": 0, "variant": {}}
    return characters


def _Text(value):
    return value if isinstance(value, str) else ""


def _Score(value):
    return value if isinstance(value, int) and not isinstance(
        value, bool) else 0


def SetData(result, event, catalog, date=None, cache=None):
    """The data thumbnail.html draws result set `result` (a set of the
    job's results: p1 / p2, s1 / s2, round, ``autona``) with, under the
    event name `event` and the date `date` (results.json's, or None).
    `catalog`: a GameAssetCatalog (None: no game name, no characters).
    `cache`: a dict the caller keeps across the sets of one run (each
    game's characters are listed once)."""
    cache = {} if cache is None else cache
    extra = result.get(NAMESPACE) if isinstance(result, dict) else None
    extra = extra if isinstance(extra, dict) else {}
    game = extra.get("game") if isinstance(extra.get("game"), str) else ""
    config = catalog.GamesView().get(game) if (
        game and catalog is not None) else None
    gameName = config.get("name") if isinstance(config, dict) else None

    def team(side):
        name = _Text(result.get(f"p{side}"))
        return {
            "score": _Score(result.get(f"s{side}")), "teamName": "",
            "losers": False,
            "player": {"1": {
                "name": name, "team": "", "mergedName": name,
                "character": _Characters(game, extra.get(f"c{side}"),
                                         catalog, cache),
                "country": {}, "state": {}}}}

    return {
        "game": {"codename": game, "name": _Text(gameName)},
        "tournamentInfo": {"tournamentName": _Text(event),
                           "startAt": _Text(date)},
        "score": {"1": {
            "phase": "", "pool": "", "match": _Text(result.get("round")),
            "team": {"1": team(1), "2": team(2)}}},
    }
