from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import requests
import os
import time
import traceback
from loguru import logger
from ..Helpers.AutonaCountryHelper import AutonaCountryHelper
from ..Helpers.AutonaDictHelper import deep_get
from ..Helpers.AutonaDirHelper import AutonaResolve
from ..AutonaGameAssetManager import AutonaGameAssetManager
from ..AutonaPlayerDB import AutonaPlayerDB
from .TournamentDataProvider import TournamentDataProvider
import orjson
from ..Helpers.AutonaLocaleHelper import AutonaLocaleHelper

from ..Workers import Worker


class StartGGDataProvider(TournamentDataProvider):
    EntrantsQuery = None
    # request for a single set with only info relevant for a set that is yet to be played
    FutureSetQuery = None
    HistorySetsQuery = None
    LastSetsQuery = None
    RecentSetsQuery = None
    SetQuery = None
    SetsQuery = None
    StationsQuery = None
    StationSetsQuery = None
    StreamSetsQuery = None
    StreamQueueQuery = None
    TournamentDataQuery = None
    TournamentPhasesQuery = None
    TournamentPhaseGroupQuery = None
    AnalysisPhaseGroupQuery = None
    TournamentStandingsQuery = None
    TournamentSlugQuery = None
    _request_timeout_secs = 20.0
    _initialized = False

    player_seeds = {}

    @staticmethod
    def Initialize():
        """Read the GraphQL query files into the class attributes above.

        Used to run at import time; it is now called explicitly from
        src/bootstrap.py so that importing this module has no side effect.
        Idempotent: the query files ship with the program and never change
        at runtime.

        AutonaResolve() resolves against the cwd captured when
        Helpers/AutonaDirHelper was imported, not against the cwd at call
        time, so moving these reads later resolves to exactly the same
        paths as the old module tail did.
        """
        if StartGGDataProvider._initialized:
            return
        StartGGDataProvider._initialized = True

        queryDir = AutonaResolve('src/TournamentDataProvider')

        def readQueryFile(filename):
            with open(f"{queryDir}/StartGG{filename}Query.txt", "r") as f:
                return f.read()

        StartGGDataProvider.EntrantsQuery = readQueryFile("Entrants")
        StartGGDataProvider.FutureSetQuery = readQueryFile("FutureSet")
        StartGGDataProvider.HistorySetsQuery = readQueryFile(
            "PlayerTournamentHistory")
        StartGGDataProvider.LastSetsQuery = readQueryFile("PlayerLastSets")
        StartGGDataProvider.RecentSetsQuery = readQueryFile("RecentSets")
        StartGGDataProvider.SetQuery = readQueryFile("Set")
        StartGGDataProvider.SetsQuery = readQueryFile("Sets")
        StartGGDataProvider.StationsQuery = readQueryFile("Stations")
        StartGGDataProvider.StationSetsQuery = readQueryFile("StationSets")
        StartGGDataProvider.StreamQueueQuery = readQueryFile("StreamQueue")
        StartGGDataProvider.StreamSetsQuery = readQueryFile("StreamSets")
        StartGGDataProvider.TournamentDataQuery = readQueryFile(
            "TournamentData")
        StartGGDataProvider.TournamentPhasesQuery = readQueryFile(
            "TournamentPhases")
        StartGGDataProvider.TournamentPhaseGroupQuery = readQueryFile(
            "TournamentPhaseGroup")
        StartGGDataProvider.AnalysisPhaseGroupQuery = readQueryFile(
            "AnalysisPhaseGroup")
        StartGGDataProvider.TournamentStandingsQuery = readQueryFile(
            "TournamentStandings")
        StartGGDataProvider.TournamentSlugQuery = readQueryFile(
            "TournamentSlug")

    def __init__(self, url, threadpool, tshTdp) -> None:
        super().__init__(url, threadpool, tshTdp)
        self.name = "StartGG"
        # In-memory cache of entrants sorted by initialSeedNum for the current
        # event, populated by GetEntrantsWorker. Each item: {"seed": int, "gamerTag": str}.
        self.top_entrants = []
        # Event headcount cached by GetTournamentData; anchors the TOP-N
        # round naming when a set's phase doesn't report numSeeds.
        self.numEntrants = 0

    # Queries the provided URL until a proper 200 status code has been provided back
    #
    # This should work fine in theory unless an API restriction is added
    def QueryRequests(self, url=None, type=None, headers={}, jsonParams=None, params=None):
        headers.update({
            "client-version": "20",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36"
        })

        # Retry on transient failures (timeouts, connection errors, non-200)
        # with exponential backoff. Start.gg's heavy queries (phaseGroup
        # with seeds+sets) occasionally take longer than the per-request
        # timeout; a short wait before retrying gives the server time to
        # settle instead of immediately hammering it again.
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                data = type(
                    url,
                    timeout=self._request_timeout_secs,
                    headers=headers,
                    json=jsonParams,
                    params=params,
                )
                if data.status_code == 200:
                    return orjson.loads(data.text)
                logger.warning(
                    f"QueryRequests non-200 ({data.status_code}) on attempt {attempt+1}/{max_attempts}")
            except Exception as e:
                logger.warning(
                    f"QueryRequests exception on attempt {attempt+1}/{max_attempts}: {e}")

            if attempt < max_attempts - 1:
                # 1s, 2s, 4s, 8s — doubles each retry
                time.sleep(2 ** attempt)

        logger.error("QueryRequests: all retries exhausted")
        return {}

    def GetTournamentData(self, progress_callback=None, cancel_event=None):
        finalData = {}

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentDataQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": StartGGDataProvider.TournamentDataQuery
                }
            )

            videogame = deep_get(data, "data.event.videogame.id", None)
            if videogame:
                self.videogame = videogame
                # Store videogame in return data so caller can emit
                # game_changed on the main thread (not from worker thread)
                finalData["_videogame"] = videogame

            finalData["tournamentName"] = deep_get(
                data, "data.event.tournament.name", "")
            finalData["eventName"] = deep_get(
                data, "data.event.name", "")
            finalData["numEntrants"] = deep_get(
                data, "data.event.numEntrants", 0)
            self.numEntrants = finalData["numEntrants"] or 0
            finalData["address"] = deep_get(
                data, "data.event.tournament.venueAddress", "")
            finalData["shortLink"] = deep_get(
                data, "data.event.tournament.shortSlug", "")
            finalData["startAt"] = deep_get(
                data, "data.event.tournament.startAt", "")
            finalData["endAt"] = deep_get(
                data, "data.event.tournament.endAt", "")
            finalData["eventStartAt"] = deep_get(
                data, "data.event.startAt", "")
            finalData["eventEndAt"] = deep_get(
                data, "data.event.endAt", "")
        except:
            logger.error(traceback.format_exc())

        return finalData

    def GetIconURL(self):
        url = None

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentIconQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": '''
                        query TournamentIconQuery($eventSlug: String!) {
                            event(slug: $eventSlug) {
                                tournament{
                                    images(type: "profile") {
                                        type
                                        url
                                    }
                                }
                            }
                        }
                    '''
                }
            )

            images = deep_get(data, "data.event.tournament.images", [])

            if len(images) > 0:
                url = images[0]["url"]
        except:
            logger.error(traceback.format_exc())

        return url

    def GetBannerURL(self):
        url = None

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentBannerQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": '''
                        query TournamentBannerQuery($eventSlug: String!) {
                            event(slug: $eventSlug) {
                                tournament{
                                    images(type: "banner") {
                                        type
                                        url
                                    }
                                }
                            }
                        }
                    '''
                }
            )

            images = deep_get(data, "data.event.tournament.images", [])

            if len(images) > 0:
                url = images[0]["url"]
        except:
            logger.error(traceback.format_exc())

        return url

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None):
        phases = []

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentPhasesQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": StartGGDataProvider.TournamentPhasesQuery
                }
            )

            logger.info(
                f"Phases loaded: {len(deep_get(data, 'data.event.phases', []) or [])} items")

            for phase in deep_get(data, "data.event.phases", []):
                phaseObj = {
                    "id": phase.get("id"),
                    "name": phase.get("name"),
                    "groups": []
                }

                for phaseGroup in deep_get(phase, "phaseGroups.nodes", []):
                    phaseObj["groups"].append({
                        "id": phaseGroup.get("id"),
                        "name": AutonaLocaleHelper.phaseNames.get("group").format(phaseGroup.get('displayIdentifier')),
                        "bracketType": phaseGroup.get("bracketType")
                    })

                phases.append(phaseObj)
        except:
            logger.error(traceback.format_exc())

        return phases

    @staticmethod
    def _parse_sets_to_rounds(sets, include_full_round_text=False):
        """Group Start.gg sets by round key into the bracket's column structure.

        Shared by GetTournamentPhaseGroup and GetAnalysisPhaseGroup. The round
        key is the authoritative column assignment (positive = winners, negative
        = losers, max = grand final, max+1 = reset). Raw slot prereq info is
        preserved so the Bracket can wire winNext/loseNext without re-deriving
        topology. include_full_round_text adds the fullRoundText field (Analysis
        dock needs it; the scoreboard Bracket does not).
        """
        sets = sets or []

        # Preview IDs cannot be sorted normally. They follow the format
        # preview_2004442_1_5, where
        # preview_2004442_1_1 < preview_2004442_1_11 < preview_2004442_1_2
        isPreview = any("preview" in str(s.get("id")) for s in sets)

        if not isPreview:
            sets.sort(key=lambda s: (abs(int(s.get("round") or 0)), s.get("id")))
        else:
            sets.sort(key=lambda s: (abs(int(s.get("round") or 0)),
                      int(str(s.get("id")).split("_")[-1])))

        finalSets = {}
        for s in sets:
            try:
                round = int(s.get("round"))
            except (TypeError, ValueError):
                continue

            if str(round) not in finalSets:
                finalSets[str(round)] = []

            slots = []
            for slot in s.get("slots", []) or []:
                slots.append({
                    "prereqType": slot.get("prereqType"),
                    "prereqId": slot.get("prereqId"),
                    "prereqPlacement": slot.get("prereqPlacement"),
                    "seedNum": deep_get(slot, "seed.seedNum"),
                })

            entry = {
                "id": s.get("id"),
                "identifier": s.get("identifier"),
                "score": [s.get("entrant1Score"), s.get("entrant2Score")],
                "finished": s.get("state", 0) == 3,
                "slots": slots,
            }
            if include_full_round_text:
                entry["fullRoundText"] = s.get("fullRoundText")

            finalSets[str(round)].append(entry)

        return finalSets

    @staticmethod
    def _split_gf_reset(finalSets, progressionsOut):
        """Split a bundled grand-final + reset into separate rounds, in-place.

        Start.gg returns the grand final reset as an extra set at the highest
        round key when a phase group has no progressions out. Separate it so the
        UI can render GF and GF Reset as distinct columns.
        """
        if not progressionsOut:
            positiveRounds = [int(r) for r in finalSets.keys() if int(r) > 0]
            if positiveRounds:
                lastRound = max(positiveRounds)
                if len(finalSets[str(lastRound)]) > 1:
                    gfsReset = finalSets[str(lastRound)].pop()
                    finalSets[str(lastRound + 1)] = [gfsReset]

    def GetTournamentPhaseGroup(self, id, progress_callback=None, cancel_event=None):
        from ..Helpers.AutonaStatusManager import AutonaStatusManager
        finalData = {}
        status_token = AutonaStatusManager.push(
            "Start.gg からブラケット情報を取得中...")

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentPhaseGroupQuery",
                    "variables": {
                        "id": id,
                    },
                    "query": StartGGDataProvider.TournamentPhaseGroupQuery
                }
            )

            seeds = deep_get(data, "data.phaseGroup.seeds.nodes", [])
            seeds.sort(key=lambda s: s.get("seedNum"))

            seedMap: list = deep_get(data, "data.phaseGroup.seedMap.1")

            if seedMap:
                seedMap = [s if s != "bye" else -1 for s in seedMap]
                finalData["seedMap"] = seedMap

            # Seed the team list by seedNum, padding gaps with placeholders
            # so teams[seedNum-1] always matches that seed's data. Start.gg
            # can return non-contiguous seed numbers (e.g. in progression
            # pools where some seeds didn't qualify), and bracket sets
            # reference players by seedNum — a position-based list would
            # misalign slot.{N} data with the bracket's player IDs.
            teams = []
            for seed in seeds:
                seedNum = seed.get("seedNum")
                if seedNum is None:
                    continue
                while len(teams) < seedNum - 1:
                    teams.append({})

                team = {}
                participants = deep_get(seed, "entrant.participants")

                if participants is not None:
                    if len(participants) > 1:
                        team["name"] = deep_get(seed, "entrant.name")

                    team["players"] = []

                    for entrant in participants:
                        team["players"].append(StartGGDataProvider.ProcessEntrantData(
                            entrant, deep_get(seed, "entrant.paginatedSets.nodes")))

                teams.append(team)

            finalData["entrants"] = teams

            finalData["sets"] = StartGGDataProvider._parse_sets_to_rounds(
                deep_get(data, "data.phaseGroup.sets.nodes", []))

            finalData["progressionsIn"] = []

            for s in seeds:
                originPhaseId = deep_get(
                    s, "progressionSource.originPhaseGroup.id")
                if originPhaseId:
                    finalData["progressionsIn"].append(originPhaseId)

            finalData["progressionsOut"] = deep_get(
                data, "data.phaseGroup.progressionsOut")

            StartGGDataProvider._split_gf_reset(
                finalData["sets"], finalData["progressionsOut"])
        except:
            logger.error(traceback.format_exc())
        finally:
            AutonaStatusManager.pop(status_token)

        return finalData

    def GetAnalysisPhaseGroup(self, id, progress_callback=None, cancel_event=None):
        """Lightweight variant of GetTournamentPhaseGroup for the Analysis
        dock. Strips user/authorization/image/main-character fields to keep
        responses well under the 1 MB timeout risk and fetches only what
        the Bracket topology + seed-table need.

        Output shape is a subset of GetTournamentPhaseGroup's return value
        so Bracket() can consume it directly:
            {"entrants": [...], "sets": {...}, "progressionsOut": [...]}
        """
        finalData = {"entrants": [], "sets": {}, "progressionsOut": []}

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "AnalysisPhaseGroupQuery",
                    "variables": {"id": id},
                    "query": StartGGDataProvider.AnalysisPhaseGroupQuery
                }
            )

            seeds = deep_get(data, "data.phaseGroup.seeds.nodes", []) or []
            seeds.sort(key=lambda s: s.get("seedNum") or 0)

            teams = []
            for seed in seeds:
                seedNum = seed.get("seedNum")
                if seedNum is None:
                    continue
                while len(teams) < seedNum - 1:
                    teams.append({})

                team = {}
                participants = deep_get(seed, "entrant.participants") or []

                if len(participants) > 1:
                    team["name"] = deep_get(seed, "entrant.name")

                team["players"] = []
                for p in participants:
                    player = (p or {}).get("player") or {}
                    team["players"].append({
                        "gamerTag": player.get("gamerTag"),
                    })

                teams.append(team)

            finalData["entrants"] = teams

            finalData["sets"] = StartGGDataProvider._parse_sets_to_rounds(
                deep_get(data, "data.phaseGroup.sets.nodes", []) or [],
                include_full_round_text=True)
            finalData["progressionsOut"] = deep_get(
                data, "data.phaseGroup.progressionsOut") or []

            StartGGDataProvider._split_gf_reset(
                finalData["sets"], finalData["progressionsOut"])
        except Exception:
            logger.error(traceback.format_exc())

        return finalData

    def GetMatch(self, setId, progress_callback=None, cancel_event=None):
        from ..Helpers.AutonaStatusManager import AutonaStatusManager
        finalResult = None
        status_token = AutonaStatusManager.push("Start.gg からセット情報を取得中...")

        try:
            result = {}

            fetchOld = Worker(self._GetMatchTasks, **{
                "progress_callback": None,
                "cancel_event": None,
                "setId": setId
            })
            fetchNew = Worker(self._GetMatchNewApi, **{
                "progress_callback": None,
                "cancel_event": None,
                "setId": setId
            })
            self.threadpool.start(fetchOld)
            self.threadpool.start(fetchNew)

            Worker.wait_for_all([fetchOld, fetchNew], self._request_timeout_secs)

            result.update({"new": fetchNew.result if fetchNew.completed else {}})
            result.update({"old": fetchOld.result if fetchOld.completed else {}})

            logger.debug(result)

            finalResult = {}
            finalResult.update(result["new"])
            finalResult.update(result["old"])

            if result.get("new", {}).get("isOnline") == False:
                finalResult["bestOf"] = None

            finalResult["entrants"] = result.get("new", {}).get("entrants", [])

            if result.get("old", {}).get("entrants", []) is not None:
                for t, team in enumerate(result.get("old", {}).get("entrants", [])):
                    for p, player in enumerate(team):
                        if player["mains"]:
                            try:
                                finalResult["entrants"][t][p]["mains"] = player["mains"]
                                finalResult["has_selection_data"] = True
                            except:
                                logger.debug(traceback.format_exc())

            logger.debug(f"Final result: {finalResult}")

        except Exception as e:
            logger.error(traceback.format_exc())
        finally:
            AutonaStatusManager.pop(status_token)
        return finalResult

    def _GetMatchTasks(self, setId, progress_callback, cancel_event):
        if "preview" in str(setId):
            return self.ParseMatchDataOldApi({})

        try:
            data = self.QueryRequests(
                f'https://www.start.gg/api/-/gg_api./set/{setId};bustCache=true;expand=["setTask"];fetchMostRecentCached=true',
                type=requests.get,
                params={
                    "extensions": {"cacheControl": {"version": 1, "noCache": True}},
                    "cacheControl": {"version": 1, "noCache": True},
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                }
            )
            return self.ParseMatchDataOldApi(data)
        except Exception as e:
            logger.error(traceback.format_exc())
            return {}

    def _GetMatchNewApi(self, setId, progress_callback, cancel_event):
        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "SetQuery",
                    "variables": {
                        "id": setId
                    },
                    "query": StartGGDataProvider.SetQuery
                }
            )
            logger.debug(data.get("data", {}).get("set", {}))
            return self.ParseMatchDataNewApi(data.get("data", {}).get("set", {}))
        except Exception as e:
            logger.error(traceback.format_exc())
            return {}

    # Concurrent page fetches for the set list: a browser-like burst the
    # internal GQL endpoint sees from normal site usage (the official API
    # allows 80 req/min, which even a 10-page fetch stays well under).
    _SETS_FETCH_WORKERS = 4

    def GetMatches(self, getFinished=False, phaseGroupIds=None, progress_callback=None, cancel_event=None):
        from ..Helpers.AutonaStatusManager import AutonaStatusManager
        status_token = AutonaStatusManager.push(
            "Start.gg からセット一覧を取得中...")
        final_data = []
        try:
            logger.info(f"Get matches (getFinished={getFinished})")
            states = [1, 6, 2]

            if getFinished:
                states.append(3)

            # Limit the fetch to specific pools (phase groups) so large
            # events don't page through every set of the whole bracket.
            filters = {
                "state": states,
                "hideEmpty": True
            }
            if phaseGroupIds:
                filters["phaseGroupIds"] = phaseGroupIds

            def fetchPage(page):
                return self.QueryRequests(
                    "https://www.start.gg/api/-/gql",
                    type=requests.post,
                    jsonParams={
                        "operationName": "EventMatchListQuery",
                        "variables": {
                            "filters": filters,
                            "eventSlug": self.url.split("start.gg/")[1],
                            "page": page,
                            "perPage": 64
                        },
                        "query": StartGGDataProvider.SetsQuery
                    }
                )

            def report(done, totalPages):
                if progress_callback:
                    progress_callback(done, totalPages)
                AutonaStatusManager.update(
                    status_token,
                    f"Start.gg からセット一覧を取得中... ({done}/{totalPages})",
                    progress=done, total=totalPages)

            logger.info("Fetching sets")

            # Page 1 synchronously to learn the page count. nodes None (as
            # opposed to []) marks a failed page — QueryRequests returns {}
            # once its own retries are exhausted.
            data = fetchPage(1)
            totalPages = deep_get(
                data, "data.event.sets.pageInfo.totalPages", 0)
            pageNodes = {1: deep_get(data, "data.event.sets.nodes", None)}
            report(1, totalPages)

            # Remaining pages concurrently. Network only in the pool; parsing
            # stays on this worker thread. No context manager: on cancel we
            # abandon in-flight requests instead of joining them (a page deep
            # in its retry backoff would otherwise block for tens of seconds).
            if totalPages > 1 and (cancel_event is None or not cancel_event.is_set()):
                pool = ThreadPoolExecutor(
                    max_workers=self._SETS_FETCH_WORKERS)
                try:
                    futures = {pool.submit(fetchPage, p): p
                               for p in range(2, totalPages + 1)}
                    done = 1
                    for future in as_completed(futures):
                        pageNodes[futures[future]] = deep_get(
                            future.result(), "data.event.sets.nodes", None)
                        done += 1
                        report(done, totalPages)
                        if cancel_event is not None and cancel_event.is_set():
                            break
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)

            if cancel_event is not None and cancel_event.is_set():
                return final_data

            # One sequential retry round for failed pages: a transient
            # throttle during the burst would otherwise silently drop their
            # sets (the old sequential loop had the same blind spot).
            for page in [p for p, nodes in pageNodes.items() if nodes is None]:
                pageNodes[page] = deep_get(
                    fetchPage(page), "data.event.sets.nodes", None)
            stillFailed = sorted(
                p for p, nodes in pageNodes.items() if nodes is None)
            if stillFailed:
                logger.error(
                    f"GetMatches: pages {stillFailed} of {totalPages} failed;"
                    " their sets are missing from the list")
                AutonaStatusManager.update(
                    status_token,
                    "セット一覧の一部ページ取得に失敗しました"
                    f" ({len(stillFailed)}/{totalPages})")

            # Assemble in page order. Live brackets can shift sets across
            # page boundaries mid-fetch, so dedupe by set id.
            seenIds = set()
            for page in sorted(pageNodes):
                for _set in (pageNodes[page] or []):
                    setId = _set.get("id")
                    if setId is not None:
                        if setId in seenIds:
                            continue
                        seenIds.add(setId)
                    final_data.append(self.ParseMatchDataNewApi(_set))
            return final_data
        except Exception as e:
            logger.error(traceback.format_exc())
            return final_data
        finally:
            AutonaStatusManager.pop(status_token)

    def GetStations(self, progress_callback=None, cancel_event=None):
        try:
            logger.info("Get stations")

            final_data = []

            logger.info("Fetching stations")

            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "Stations",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1],
                    },
                    "query": StartGGDataProvider.StationsQuery
                }
            )

            stations = deep_get(data, "data.event.stations.nodes", [])
            queues = deep_get(data, "data.event.tournament.streamQueue", [])

            if stations is not None:
                for station in stations:
                    stream = ""

                    if queues is not None:
                        stream = next((deep_get(s, "stream.streamName", None) for s in queues if str(
                            deep_get(s, "stream.id", None)) == str(station.get("streamId"))), "")

                    final_data.append({
                        "id": station.get("id"),
                        "identifier": station.get("number"),
                        "type": "station",
                        "stream": stream
                    })

            if queues is not None:
                for queue in queues:
                    if queue.get("stream") is not None:
                        stream = queue.get("stream")
                        final_data.append({
                            "id": stream.get("id"),
                            "identifier": stream.get("streamName"),
                            "type": "stream"
                        })

            return (final_data)
        except Exception as e:
            logger.error(traceback.format_exc())
            return (final_data)

    # Descending "players still alive" ladder for numbered losers rounds:
    # the 2^n and 3*2^n series interleaved, one step per round, extended below
    # 8 so the deepest rounds land on the conventional names (6 -> LQF,
    # 4 -> LSF, 3 -> LF via TOP_NAME_REMAP). Values above 8 must stay in sync
    # with the match combo candidates (AutonaLocaleHelper.LOSERS_TOP_TOTALS
    # plus Losers TOP8; the sub-8 values surface as the named finals, which
    # are combo candidates already).
    LosersTopLadder = sorted(
        {2 ** n for n in range(2, 14)} | {3 * 2 ** n for n in range(0, 12)},
        reverse=True)

    # Alive-count values that correspond to the conventional round names.
    # The invariant "N = 1.5x the winners-side headcount" makes these
    # phase-independent: the true quarter-final always happens at 12 alive
    # (8W/4L) no matter how the event is split into phases, so a phase-local
    # "Winners Quarter-Final" from an earlier phase converts to its honest
    # TOP-N while the genuinely-final rounds round-trip back to their names.
    WINNERS_TOP_NAME_REMAP = {
        12: ("winners_quarter_final", "Winners Quarter-Final"),
        6: ("winners_semi_final", "Winners Semi-Final"),
        3: ("winners_final", "Winners Final"),
    }
    LOSERS_TOP_NAME_REMAP = {
        8: ("losers_top8", "Losers TOP8"),
        6: ("losers_quarter_final", "Losers Quarter-Final"),
        4: ("losers_semi_final", "Losers Semi-Final"),
        3: ("losers_final", "Losers Final"),
    }

    def RoundNumberToTopName(roundNumber, numEntrants, losers=False,
                             presplit=False):
        """Map a numbered double-elimination round to 'Winners TOP{N}' /
        'Losers TOP{N}', where N is the players still alive when the round
        starts, anchored at the real entrant count: with 300 entrants the
        winners side runs TOP300 -> TOP192 -> TOP96 -> ... and the losers
        side TOP300 -> TOP256 -> TOP192 -> ...

        presplit marks a later phase whose field enters already split between
        the winners and losers sides (a TOP64 phase seeds 32W/32L, a TOP96
        phase 32W/64L). There the leading losers rounds eliminate before the
        winners side ever plays, so winners round 1 skips the TOP{entrants}
        step and starts one rung down the 3*2^n ladder: TOP64 -> Winners
        TOP48, TOP96 -> Winners TOP48. The losers ladder is unaffected — its
        round 1 is the phase's first elimination either way.

        Alive counts that correspond to the true finals come back as the
        conventional names instead of TOP-N (see *_TOP_NAME_REMAP): Winners
        12/6/3 -> WQF/WSF/WF, Losers 8/6/4/3 -> TOP8/LQF/LSF/LF.

        Returns None when the notation can't be derived (unknown entrant
        count, non-numeric round, or the walk runs past the last rung)."""
        try:
            round_ = int(roundNumber)
            entrants = int(numEntrants or 0)
        except (TypeError, ValueError):
            return None
        if round_ <= 0 or entrants < 8:
            return None

        if losers:
            # Losers round 1 starts before anyone is out: its players carry a
            # single loss, so the whole field is still alive. Anchor the ladder
            # at the entrant count like the winners side rather than starting a
            # step in.
            ladder = [entrants] + [v for v in StartGGDataProvider.LosersTopLadder
                                   if v < entrants]
            if round_ > len(ladder):
                return None
            value = ladder[round_ - 1]
            remap = StartGGDataProvider.LOSERS_TOP_NAME_REMAP.get(value)
            # ladder[0] is the raw entrant count, not an alive-count rung on
            # the 1.5x-winners invariant — never remap it to a named round.
            if remap and round_ > 1:
                return AutonaLocaleHelper.matchNames.get(*remap)
            if value == 8:
                return AutonaLocaleHelper.matchNames.get(
                    "losers_top8", "Losers TOP8")
            return AutonaLocaleHelper.matchNames.get(
                "losers_top_n", "Losers TOP{0}").format(value)

        if round_ == 1 and not presplit:
            return AutonaLocaleHelper.matchNames.get(
                "winners_top_n", "Winners TOP{0}").format(entrants)
        # Largest 3*2^n strictly below the entrant count, halved once per
        # later round (presplit: winners round 1 already sits on the ladder).
        value = 3
        while value * 2 < entrants:
            value *= 2
        value >>= (round_ - 1) if presplit else (round_ - 2)
        if value < 3:
            return None
        remap = StartGGDataProvider.WINNERS_TOP_NAME_REMAP.get(value)
        if remap:
            return AutonaLocaleHelper.matchNames.get(*remap)
        return AutonaLocaleHelper.matchNames.get(
            "winners_top_n", "Winners TOP{0}").format(value)

    def TranslateRoundName(name: str, numEntrants: int = 0,
                           presplit: bool = False, roundNumber=None):
        """Translate a Start.gg fullRoundText. Pass the double-elimination
        bracket's headcount as numEntrants to render numbered rounds in the
        TOP-N notation (0 keeps the plain 'Winners Round {N}' fallback);
        presplit as in RoundNumberToTopName.

        roundNumber is the set's numeric ``round`` field (negative = losers
        side). Start.gg names the deepest rounds of EVERY phase bracket
        WQF/WSF/WF/LQF/..., so an early phase's "Winners Semi-Final" is only
        phase-local. With roundNumber those named rounds are pushed through
        the same alive-count conversion as numbered ones: phase-local finals
        become their honest TOP-N while the event's true finals land on the
        remap values and round-trip back to the conventional names. Grand
        Final (Reset) only exists in the last phase and stays named."""
        if name == None:
            return ""

        roundMapping = {
            "Grand Final Reset": "grand_final_reset",
            "Grand Final": "grand_final",
            "Winners Final": "winners_final",
            "Winners Semi-Final": "winners_semi_final",
            "Winners Quarter-Final": "winners_quarter_final",
            "Losers Final": "losers_final",
            "Losers Semi-Final": "losers_semi_final",
            "Losers Quarter-Final": "losers_quarter_final"
        }

        if name in roundMapping:
            if roundNumber and not name.startswith("Grand Final"):
                topName = StartGGDataProvider.RoundNumberToTopName(
                    abs(roundNumber), numEntrants,
                    losers=roundNumber < 0, presplit=presplit)
                if topName:
                    return topName
            return AutonaLocaleHelper.matchNames.get(roundMapping.get(name))

        try:
            roundNumber = name.rsplit(" ")[-1]

            if "Winners" in name:
                topName = StartGGDataProvider.RoundNumberToTopName(
                    roundNumber, numEntrants, presplit=presplit)
                if topName:
                    return topName
                return AutonaLocaleHelper.matchNames.get("winners_round").format(roundNumber)
            elif "Losers" in name:
                topName = StartGGDataProvider.RoundNumberToTopName(
                    roundNumber, numEntrants, losers=True)
                if topName:
                    return topName
                return AutonaLocaleHelper.matchNames.get("losers_round").format(roundNumber)
            elif name.startswith("Round "):
                return AutonaLocaleHelper.matchNames.get("round").format(roundNumber)
        except:
            logger.error(traceback.format_exc())

        return name

    def _build_set_metadata(self, _set, p1, p2):
        """Extract set-level metadata from a new API response."""
        phase_name = deep_get(_set, "phaseGroup.phase.name")
        bracket_type = deep_get(_set, "phaseGroup.phase.bracketType", "")

        # TOP-N anchor: the headcount of this set's own phase when the query
        # provides it (correct after pools), else the whole event. Only
        # meaningful for double elimination. A phase smaller than the event
        # is a later (progression) phase whose field enters pre-split between
        # the winners and losers sides.
        topAnchor = 0
        presplit = False
        if bracket_type == "DOUBLE_ELIMINATION":
            phaseSeeds = deep_get(_set, "phaseGroup.phase.numSeeds") or 0
            topAnchor = phaseSeeds or self.numEntrants
            presplit = 0 < phaseSeeds < self.numEntrants

        round_name = StartGGDataProvider.TranslateRoundName(
            _set.get("fullRoundText"), topAnchor, presplit=presplit,
            roundNumber=_set.get("round"))

        if bracket_type == "MATCHMAKING":
            round_name = ""

        # Pool (phase group) name is kept SEPARATE from the phase name:
        # `tournament_phase` is the phase only and `tournament_pool` the
        # localized group name ("" when the phase has a single group).
        # Consumers that want the legacy combined text join them for display.
        isPools = deep_get(_set, "phaseGroup.phase.groupCount", 0) > 1
        pool_name = ""
        if isPools:
            pool_name = AutonaLocaleHelper.phaseNames.get(
                "group").format(deep_get(_set, "phaseGroup.displayIdentifier"))

        return {
            "id": _set.get("id"),
            "team1score": _set.get("entrant1Score", 0),
            "team2score": _set.get("entrant2Score", 0),
            "round_name": round_name,
            "round_name_en": _set.get("fullRoundText") or "",
            "tournament_phase": phase_name,
            "tournament_pool": pool_name,
            "bracket_type": bracket_type,
            "p1_name": p1.get("entrant", {}).get("name", "") if p1 and p1.get("entrant") != None else "",
            "p2_name": p2.get("entrant", {}).get("name", "") if p2 and p2.get("entrant") != None else "",
            "p1_seed": p1.get("entrant", {}).get("initialSeedNum", None) if p1 and p1.get("entrant") else None,
            "p2_seed": p2.get("entrant", {}).get("initialSeedNum", None) if p2 and p2.get("entrant") else None,
            "stream": deep_get(_set, "stream.streamName") or "",
            "station": _set.get("station", {}).get("number", "") if _set.get("station", {}) != None else "",
            "isOnline": deep_get(_set, "event.isOnline"),
            "isPools": isPools,
            "round": _set.get("round")
        }

    def _parse_participant_new_api(self, entrant, teamIndex, setData):
        """Parse a single participant from a new API entrant dict."""
        player = entrant.get("player")
        user = entrant.get("user")

        playerData = {}

        if player:
            playerData["prefix"] = player.get("prefix")
            playerData["gamerTag"] = player.get("gamerTag")
            playerData["name"] = player.get("name")

            # Main character
            playerSelections = Counter()

            sets = deep_get(player, "sets.nodes", [])
            playerId = player.get("id")
            if len(sets) > 0:
                games = sets[0].get("games", [])
                if games and len(games) > 0:
                    for game in games:
                        selections = game.get("selections", [])
                        if selections:
                            for selection in selections:
                                participants = selection.get(
                                    "entrant", {}).get("participants", [])
                                if len(participants) > 0:
                                    participantId = participants[0].get(
                                        "player", {}).get("id", None)
                                    if participantId and participantId == playerId:
                                        playerSelections[selection.get(
                                            "selectionValue")] += 1

            main = playerSelections.most_common(1)

            if len(main) > 0:
                playerData["startggMain"] = main[0][0]

        if user:
            if user.get("authorizations"):
                if len(user.get("authorizations", [])) > 0:
                    playerData["twitter"] = user.get("authorizations", [])[
                        0].get("externalUsername")

            if user.get("images") and len(user.get("images")) > 0:
                playerData["avatar"] = user.get("images")[
                    0].get("url")

            if user.get("id"):
                playerData["id"] = [
                    player.get("id"),
                    user.get("id")
                ]

            if user.get("location"):
                # Country to country code
                if user.get("location").get("country"):
                    for country in AutonaCountryHelper.countries.values():
                        if user.get("location").get("country") == country.get("en_name"):
                            playerData["country_code"] = country.get(
                                "code")
                            break

                # State. The city fallback is back, now that it costs an
                # O(1) lookup in the pre-computed table
                # (assets/city_to_state.json, lazily loaded once) instead of
                # a linear scan over the country's state/city tables per
                # entrant. It matters because the typical Japanese user has
                # state null and the region in city ("Tokyo", "Shibuya-ku").
                # The direct field goes through the same table so that a
                # name ("Tokyo") is normalized to the iso2 the state combo
                # boxes match on.
                state_raw = user.get("location").get("state")
                city_raw = user.get("location").get("city")
                country = playerData.get("country_code")
                if state_raw:
                    # A state Start.gg does know still goes through the table,
                    # so a name ("Tokyo") becomes the iso2 the state combo
                    # matches on. When it resolves to nothing -- a region this
                    # dataset has no entry for -- the city gets a turn before
                    # the raw string is published as a last resort.
                    resolved = AutonaCountryHelper.FindState(
                        country, state_raw) or (
                        AutonaCountryHelper.FindState(country, city_raw)
                        if city_raw else None)
                    playerData["state_code"] = resolved or state_raw
                elif city_raw and country:
                    resolved = AutonaCountryHelper.FindState(country, city_raw)
                    if resolved:
                        playerData["state_code"] = resolved
                if city_raw:
                    playerData["city"] = city_raw

            if playerData.get("startggMain"):
                main = AutonaGameAssetManager.instance.GetCharacterFromStartGGId(
                    playerData.get("startggMain"))
                if main:
                    playerData["mains"] = main[0]

        if "id" not in playerData:
            playerData["id"] = [
                player.get("id"),
                0
            ]
        if playerData.get("id") and len(playerData.get("id")) > 0:
            if playerData["id"][0] is not None:
                playerData["seed"] = self.player_seeds.get(
                    playerData["id"][0])

        if setData.get(f"p{teamIndex+1}_seed"):
            playerData["seed"] = setData.get(f"p{teamIndex+1}_seed")

        return playerData

    def ParseMatchDataNewApi(self, _set):
        try:
            slots = deep_get(_set, "slots", [])
            p1 = slots[0] if len(slots) > 0 else {}
            p2 = slots[1] if len(slots) > 1 else {}

            setData = self._build_set_metadata(_set, p1, p2)

            players = [[], []]

            entrants = [
                p1.get(
                    "entrant", {}).get("participants", []) if p1.get(
                    "entrant", {}) is not None else [],
                p2.get(
                    "entrant", {}).get("participants", []) if p2.get(
                    "entrant", {}) is not None else [],
            ]

            for i, team in enumerate(entrants):
                for j, entrant in enumerate(team):
                    playerData = self._parse_participant_new_api(entrant, i, setData)
                    players[i].append(playerData)

            setData["entrants"] = players

            return setData
        except Exception as e:
            logger.error(traceback.format_exc())
            return {}

    @staticmethod
    def _extract_char_selections(tasks, entrant1Id, entrant2Id):
        """Extract character selections from set tasks (old API format).

        Returns selectedChars as [[p1_chars], [p2_chars]].
        """
        selectedCharMap = {}

        for task in reversed(tasks):
            if task.get("action") in ["setup_character", "setup_strike", "setup_ban"]:
                selectedCharMap = task.get(
                    "metadata", {}).get("charSelections", {})
                if len(selectedCharMap) > 0:
                    break
            elif task.get("action") in ["report"]:
                allSelections = task.get("metadata", {}).get(
                    "report", {}).get("selections", [])

                if isinstance(allSelections, list):
                    for selection in allSelections:
                        if selection.get("selectionType") == "character":
                            selectedCharMap[str(selection.get("entrantId"))] = [
                                selection.get("selectionValue")]
                    if len(selectedCharMap) > 0:
                        break

        logger.debug(f"selectedCharMap: {selectedCharMap}")
        selectedChars = [[], []]

        for char in selectedCharMap.items():
            if str(char[0]) == entrant1Id:
                selectedChars[0] = char[1]
            if str(char[0]) == entrant2Id:
                selectedChars[1] = char[1]

        return selectedChars

    @staticmethod
    def _find_latest_winner(tasks):
        """Find the latest winner ID from set tasks (old API format)."""
        for task in reversed(tasks):
            if len(task.get("metadata", [])) == 0:
                continue
            if task.get("active"):
                continue
            if task.get("metadata", {}).get("report", {}).get("winnerId", None) is not None:
                return int(task.get("metadata", {}).get(
                    "report", {}).get("winnerId"))
        return None

    @staticmethod
    def _parse_stage_strike(tasks, entrant1Id, entrant2Id, latestWinner, scores):
        """Parse stage strike state from set tasks (old API format).

        Thin orchestrator: compute the last-winner slot, reverse-scan the tasks
        for raw strike state, then resolve IDs and build the result dicts.

        Returns (stageStrikeState, rulesetState, strikedBy, currPlayer, lastWinnerSlot).
        """
        lastWinnerSlot = None
        if latestWinner:
            if str(latestWinner) == entrant1Id:
                lastWinnerSlot = 0
            if str(latestWinner) == entrant2Id:
                lastWinnerSlot = 1

        (allStages, strikedStages, selectedStage, stageWins, dsr, mdsr,
         strikedBy, currPlayer) = StartGGDataProvider._scan_stage_strike_tasks(
            tasks, entrant1Id, entrant2Id, latestWinner)

        stageStrikeState, rulesetState, strikedBy, currPlayer = \
            StartGGDataProvider._build_stage_strike_dicts(
                allStages, strikedStages, selectedStage, stageWins, dsr, mdsr,
                strikedBy, currPlayer, lastWinnerSlot, scores)

        return stageStrikeState, rulesetState, strikedBy, currPlayer, lastWinnerSlot

    @staticmethod
    def _scan_stage_strike_tasks(tasks, entrant1Id, entrant2Id, latestWinner):
        """Reverse-scan set tasks to extract raw stage-strike state.

        Returns (allStages, strikedStages, selectedStage, stageWins, dsr, mdsr,
        strikedBy, currPlayer).
        """
        allStages = None
        strikedStages = None
        strikedBy = [[], []]
        selectedStage = None
        currPlayer = 0
        dsr = False
        mdsr = False
        stageWins = [[], []]

        for task in reversed(tasks):
            if task.get("action") in ["setup_strike", "setup_stage", "setup_character", "setup_ban", "report"]:
                if len(task.get("metadata", [])) == 0:
                    continue

                base = task.get("metadata", {})

                if task.get("action") == "report":
                    base = base.get("report", {})

                if base.get("strikeStages", None) is not None:
                    allStages = base.get("strikeStages")
                elif base.get("banStages", None) is not None:
                    allStages = base.get("banStages")

                if base.get("strikeList", None) is not None:
                    strikedStages = base.get("strikeList")
                elif base.get("banList", None) is not None:
                    strikedStages = base.get("banList")

                if base.get("stageSelection", None) is not None:
                    selectedStage = base.get("stageSelection")
                elif base.get("stageId", None) is not None:
                    selectedStage = base.get("stageId")

                # Stages previously won in
                if base.get("stageWins"):
                    stageWins = [[], []]
                    for entrantId, stageCodes in base.get("stageWins").items():
                        stages = []

                        for stageCode in stageCodes:
                            stages.append(AutonaGameAssetManager.instance.GetStageFromStartGGId(
                                int(stageCode))[1].get("codename"))

                        if str(entrantId) == entrant1Id:
                            stageWins[0] = stages
                        if str(entrantId) == entrant2Id:
                            stageWins[1] = stages

                if base.get("useMDSR"):
                    mdsr = True
                if base.get("useDSR"):
                    dsr = True

                if base.get("strikeList"):
                    for stage_code, entrant in base.get("strikeList").items():
                        stage = AutonaGameAssetManager.instance.GetStageFromStartGGId(
                            int(stage_code))

                        if stage:
                            codename = stage[1].get("codename")

                            if str(entrant) == entrant1Id:
                                strikedBy[0].append(codename)
                            if str(entrant) == entrant2Id:
                                strikedBy[1].append(codename)
                else:
                    banPlayer = 0

                    if str(latestWinner) == entrant1Id:
                        banPlayer = 0
                    if str(latestWinner) == entrant2Id:
                        banPlayer = 1

                    if base.get("banList", None) is not None:
                        for stage_code in base.get("banList"):
                            if stage_code == None:
                                continue
                            stage = AutonaGameAssetManager.instance.GetStageFromStartGGId(
                                int(stage_code))
                            if stage:
                                codename = stage[1].get("codename")
                                strikedBy[banPlayer].append(codename)

                if latestWinner:
                    lastLoserSlot = 0

                    if str(latestWinner) == entrant1Id:
                        lastLoserSlot = 1
                    if str(latestWinner) == entrant2Id:
                        lastLoserSlot = 0

                    currPlayer = lastLoserSlot
                elif base.get("turn"):
                    for entrant, value in base.get("turn").items():
                        if str(entrant) == entrant1Id and value == True:
                            currPlayer = 1
                        if str(entrant) == entrant2Id and value == True:
                            currPlayer = 0

                if allStages == None and strikedStages == None and selectedStage == None:
                    continue

                if allStages == None:
                    continue

                break

        return allStages, strikedStages, selectedStage, stageWins, dsr, mdsr, strikedBy, currPlayer

    @staticmethod
    def _build_stage_strike_dicts(allStages, strikedStages, selectedStage, stageWins, dsr, mdsr, strikedBy, currPlayer, lastWinnerSlot, scores):
        """Resolve stage IDs to codenames and build the stage-strike and ruleset
        dicts. On any resolution error, reset the strike state to empty.

        Returns (stageStrikeState, rulesetState, strikedBy, currPlayer).
        """
        try:
            allStagesFinal = []
            for st in allStages:
                stage = AutonaGameAssetManager.instance.GetStageFromStartGGId(
                    st)
                if stage:
                    allStagesFinal.append(stage[1])

            striked = []
            if strikedStages is not None:
                for stage in strikedStages:
                    stage = AutonaGameAssetManager.instance.GetStageFromStartGGId(
                        stage)
                    if stage:
                        striked.append(stage[1].get("codename"))

            selected = ""
            if selectedStage is not None:
                selectedStage = AutonaGameAssetManager.instance.GetStageFromStartGGId(
                    int(selectedStage))
                if selectedStage:
                    selected = selectedStage[1].get("codename")

            stageStrikeState = {
                "strikedBy": strikedBy,
                "strikedStages": [striked],
                "stagesWon": stageWins,
                "selectedStage": selected,
                "currPlayer": currPlayer,
                "lastWinner": lastWinnerSlot,
                "currGame": scores[0] + scores[1]
            }

            rulesetState = {
                "neutralStages": allStagesFinal,
                "useDSR": dsr,
                "useMDSR": mdsr,
            }
        except:
            logger.info("No Stage Strike Info Found")
            strikedBy = [[], []]
            currPlayer = 0
            stageStrikeState = {}
            rulesetState = {}

        return stageStrikeState, rulesetState, strikedBy, currPlayer

    @staticmethod
    def _build_entrants_old_api(selectedChars, sets):
        """Build entrant character data from old API char selections."""
        entrants = [[], []]

        def mainFor(char):
            """["<character key>", skin] for a start.gg character id, or None.

            GetCharacterFromStartGGId returns None for every id the installed
            game assets do not know -- no asset pack installed, a modded
            roster, a character start.gg added since. Subscripting that None
            raised TypeError out of the whole legacy half, and the caller
            swallows it into an empty dict: one unrecognised character cost
            the set its bestOf, stage_strike, losers flags and currPlayer too.
            """
            character = AutonaGameAssetManager.instance.GetCharacterFromStartGGId(
                char)
            if character is None:
                logger.warning(
                    f"No installed character matches start.gg id {char};"
                    " skipping its selection")
                return None
            return [character[0], 0]

        for i, entrantChars in enumerate(selectedChars):
            for char in entrantChars:
                main = mainFor(char)
                if main is not None:
                    entrants[i].append({"mains": main})

        # If we don't have mains from the selection data, fallback to the characterIds attribute
        for i in [0, 1]:
            if len(entrants[i]) == 0:
                characterIds = deep_get(
                    sets, f"entrant{i+1}CharacterIds", []) or []

                for char in characterIds:
                    try:
                        main = mainFor(char)
                        if main is not None:
                            entrants[i] = [{"mains": main}]
                    except:
                        logger.error(traceback.format_exc())

        return entrants

    def ParseMatchDataOldApi(self, respTasks):
        entities = respTasks.get("entities", {})
        sets = entities.get("sets", {})
        tasks = entities.get("setTask", [])

        entrant1Id = str(sets.get("entrant1Id"))
        entrant2Id = str(sets.get("entrant2Id"))

        selectedChars = self._extract_char_selections(tasks, entrant1Id, entrant2Id)
        latestWinner = self._find_latest_winner(tasks)

        scores = (sets.get("entrant1Score", 0), sets.get("entrant2Score", 0))
        stageStrikeState, rulesetState, strikedBy, currPlayer, lastWinnerSlot = \
            self._parse_stage_strike(tasks, entrant1Id, entrant2Id, latestWinner, scores)

        entrants = self._build_entrants_old_api(selectedChars, sets)

        team1losers = False
        team2losers = False

        if sets.get("isGF", False):
            if "Reset" not in sets.get("fullRoundText", ""):
                team1losers = False
                team2losers = True
            else:
                team1losers = True
                team2losers = True

        logger.info("Team 1 Score - OLD API: " + str(sets.get("entrant1Score", 0)))
        logger.info("Team 2 Score - OLD API: " + str(sets.get("entrant2Score", 0)))

        return ({
            "stage_strike": stageStrikeState,
            "ruleset": rulesetState,
            "strikedBy": strikedBy,
            "entrants": entrants,
            "team1score": sets.get("entrant1Score",  0),
            "team2score": sets.get("entrant2Score", 0),
            "bestOf": sets.get("bestOf", None),
            "team1losers": team1losers,
            "team2losers": team2losers,
            "currPlayer": currPlayer
        })

    def ProcessFutureSet(self, _set, eventSlug):
        # Same phase/pool separation as _build_set_metadata.
        phase_name = deep_get(_set, "phaseGroup.phase.name")
        pool_name = ""
        if deep_get(_set, "phaseGroup.phase.groupCount") > 1:
            pool_name = AutonaLocaleHelper.phaseNames.get(
                "group").format(deep_get(_set, "phaseGroup.displayIdentifier"))

        frt = _set.get("fullRoundText", "")
        total_games = _set.get("totalGames", 0)
        seteventSlug = deep_get(_set, "event.slug", "")

        # TOP-N anchor (see _build_set_metadata). The cached event headcount
        # only applies to sets from the currently loaded event, and so does
        # the pre-split (later phase) detection.
        topAnchor = 0
        presplit = False
        if deep_get(_set, "phaseGroup.phase.bracketType") == "DOUBLE_ELIMINATION":
            topAnchor = deep_get(_set, "phaseGroup.phase.numSeeds") or 0
            if seteventSlug == eventSlug:
                presplit = 0 < topAnchor < self.numEntrants
                if not topAnchor:
                    topAnchor = self.numEntrants

        setData = {
            "id": _set.get("id"),
            "match": StartGGDataProvider.TranslateRoundName(
                frt, topAnchor, presplit=presplit,
                roundNumber=_set.get("round")),
            "phase": phase_name,
            "pool": pool_name,
            "best_of": total_games,
            "best_of_text": AutonaLocaleHelper.matchNames.get("best_of").format(total_games) if total_games > 0 else "",
            "state": _set.get("state"),
            "team": {},
            "station": deep_get(_set, "station.number", -1),
            "event": seteventSlug,
            "isCurrentEvent": seteventSlug == eventSlug
        }

        for teamIndex, slot in enumerate(_set.get("slots", [])):
            entrant = slot.get("entrant", None)
            if entrant:

                losers = False
                if "Gran" in frt:
                    if teamIndex == 1 or "Reset" in frt:
                        losers = True

                teamData = {
                    "teamName": entrant.get("name", ""),
                    "losers": losers,
                    "seed": entrant.get("initialSeed", 889977666),
                    "player": {}
                }

                # TODO : pull the state data

                for playerIndex, participant in enumerate(entrant.get("participants", [])):
                    playerData = StartGGDataProvider.ProcessEntrantData(
                        participant)
                    playerName = playerData.get("gamerTag", "")
                    team = playerData.get("prefix", "")

                    countryCode = playerData.get(
                        "country_code", "")
                    stateCode = playerData.get("state_code", "")
                    countryData = AutonaCountryHelper.countries.get(
                        countryCode)
                    stateData = {}
                    if countryData:
                        states = countryData.get("states")
                        # .get(), not [...]: state_code can be a raw Start.gg
                        # string the country's state table has no entry for
                        # (ProcessEntrantData publishes the raw value when
                        # FindState cannot resolve it), and a KeyError here
                        # would take the whole standings load down.
                        if states and stateCode:
                            stateData = states.get(stateCode) or {}

                        if stateData:
                            path = f'./assets/state_flag/{countryCode}/{"_CON" if stateCode == "CON" else stateCode}.png'
                            if not os.path.exists(path):
                                path = None

                            stateData.update({
                                "asset": path
                            })

                    playerData = {
                        "country": AutonaCountryHelper.GetBasicCountryInfo(countryCode),
                        "state": stateData,
                        "name": playerName,
                        "team": team,
                        "mergedName": team + "|" + playerName if isinstance(team, str) and team != "" else playerName,
                        "real_name": playerData.get("name", ""),
                        "online_avatar": playerData.get("avatar", ""),
                        "twitter":  playerData.get("twitter", "")
                    }

                    teamData["player"][str(
                        playerIndex + 1)] = playerData

                setData["team"][str(teamIndex + 1)] = teamData
        return setData

    def GetStreamQueue(self, progress_callback=None, cancel_event=None):
        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "StreamQueueQuery",
                    "variables": {
                        "slug": self.url.split("start.gg/")[1]
                    },
                    "query": StartGGDataProvider.StreamQueueQuery
                }
            )
            logger.info("Stream queue loaded from StartGG")

            eventSlug = deep_get(data, "data.event.slug", "")
            queues = deep_get(data, "data.event.tournament.streamQueue", [])

            finalData = {}

            if not queues:
                logger.info("(No stream queue was found)")
                return finalData

            for q in queues:
                streamName = q.get("stream", {}).get("streamName", "")
                queueData = {}
                for setIndex, _set in enumerate(q.get("sets", [])):

                    setData = self.ProcessFutureSet(_set, eventSlug)

                    queueData[str(setIndex + 1)] = setData

                finalData[streamName] = queueData

            return finalData

        except Exception as e:
            logger.error(traceback.format_exc())

        return {}

    def GetStreamMatchId(self, streamName):
        streamSet = None

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "StreamSetsQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": StartGGDataProvider.StreamSetsQuery
                }
            )

            eventId = deep_get(data, "data.event.id", None)

            queues = deep_get(data, "data.event.tournament.streamQueue", [])

            if queues:
                lStreamName = streamName.lower()  # """performance"""
                queue = next(
                    (q for q in queues if q and q.get("stream", {}).get(
                        "streamName", "").lower() == lStreamName),
                    None
                )

                if queue and len(queue.get("sets")) > 0:
                    queueSets = [s for s in queue.get("sets") if deep_get(
                        s, "event.id") == eventId]
                    if len(queueSets) > 0:
                        streamSet = queueSets[0]
        except Exception as e:
            logger.error(traceback.format_exc())

        return streamSet

    def GetStationMatchsId(self, stationId):
        sets = None

        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "StationSetsQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1],
                        "filters": {
                            "state": [1, 2, 4, 5, 6],
                            "hideEmpty": True
                        }
                    },
                    "query": StartGGDataProvider.StationSetsQuery
                }
            )

            sets = deep_get(data, "data.event.sets.nodes", [])

            sets = [s for s in sets if str(deep_get(
                s, "station.id", "-1")) == str(stationId)]

        except Exception as e:
            logger.error(traceback.format_exc())

        return sets

    def GetEntrants(self):
        worker = Worker(self.GetEntrantsWorker, **{
            "eventSlug": self.url.split("start.gg/")[1]
        })
        self.threadpool.start(worker)

    def GetLastSets(self, playerID, playerNumber, callback, progress_callback, cancel_event):
        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "PlayerLastSetsQuery",
                    "variables": {
                        "eventSlug": self.url.split("start.gg/")[1],
                        "playerID": playerID
                    },
                    "query": StartGGDataProvider.LastSetsQuery
                }
            )

            sets = deep_get(
                data, "data.event.sets.nodes", [])

            set_data = []

            for set in sets:
                if not set:
                    continue
                if not set.get("winnerId"):
                    continue
                if len(set.get("slots", [])) < 2:
                    continue

                phaseName = ""
                phaseIdentifier = ""

                # This is because a display identifier at a major (Ex. Pools C12) will return C12,
                # otherwise startgg will just return a string containing "1"
                if deep_get(set, "phaseGroup.displayIdentifier") != "1":
                    phaseIdentifier = deep_get(
                        set, "phaseGroup.displayIdentifier")
                phaseName = deep_get(set, "phaseGroup.phase.name")

                player1Info = set.get("slots", [{}])[0].get("entrant", {}).get(
                    "participants", [{}])[0].get("player", {})
                player1Seed = set.get(
                    "slots", [{}])[0].get("initialSeedNum", 0)

                player2Info = set.get("slots", [{}, {}])[1].get("entrant", {}).get(
                    "participants", [{}])[0].get("player", {})
                player2Seed = set.get(
                    "slots", [{}])[1].get("initialSeedNum", 0)

                players = ["1", "2"]

                if player1Info.get("id") != playerID:
                    players.reverse()

                player_set = {
                    "phase_id": phaseIdentifier,
                    "phase_name": phaseName,
                    "round_name": StartGGDataProvider.TranslateRoundName(set.get("fullRoundText")),
                    f"player{players[0]}_score": set.get("entrant1Score") if set.get("entrant1Score") is not None else "0",
                    f"player{players[0]}_seed": player1Seed,
                    f"player{players[0]}_team": player1Info.get("prefix"),
                    f"player{players[0]}_name": player1Info.get("gamerTag"),
                    f"player{players[1]}_score": set.get("entrant2Score") if set.get("entrant2Score") is not None else "0",
                    f"player{players[1]}_seed": player2Seed,
                    f"player{players[1]}_team": player2Info.get("prefix"),
                    f"player{players[1]}_name": player2Info.get("gamerTag")
                }

                set_data.append(player_set)

            callback.emit(
                {"playerNumber": playerNumber, "last_sets": set_data})
        except Exception as e:
            logger.error(traceback.format_exc())
            callback.emit({"playerNumber": playerNumber, "last_sets": []})

    def GetPlayerHistoryStandings(self, playerID, playerNumber, gameType, callback, progress_callback, cancel_event):
        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentHistoryDataQuery",
                    "variables": {
                        "playerID": playerID,
                        "gameID": gameType
                    },
                    "query": StartGGDataProvider.HistorySetsQuery
                }
            )

            sets = deep_get(
                data, "data.player.recentStandings", [])

            set_data = []

            for set in sets:
                if not set:
                    continue
                if not set.get("placement"):
                    continue

                event = deep_get(set, "entrant.event", [])
                tournament = deep_get(event, "tournament", [])

                try:
                    tournamentPicture = tournament.get("images")[0].get("url")
                except:
                    tournamentPicture = None
                    logger.error("Failed to get Event Logo for: " +
                                 tournament.get("name") + " - " + event.get("name"))

                player_history = {
                    "placement": set.get("placement"),
                    "event_name": event.get("name"),
                    "tournament_name": tournament.get("name"),
                    "tournament_picture": tournamentPicture,
                    "entrants": event.get("numEntrants"),
                    "event_date": event.get("startAt")
                }

                set_data.append(player_history)

            callback.emit({"playerNumber": playerNumber,
                          "history_sets": set_data})
        except Exception as e:
            callback.emit({"playerNumber": playerNumber, "history_sets": []})

    def GetRecentSets(self, id1, id2, videogame, callback, requestTime, progress_callback, cancel_event):
        try:
            id1 = [str(id1[0]), str(id1[1])]
            id2 = [str(id2[0]), str(id2[1])]

            recentSets = []

            logger.info("Get recent sets start")

            for _id1, _id2, inverted in [[id1, id2, False], [id2, id1, True]]:
                workers = []
                for i in range(5):
                    worker = Worker(self.GetRecentSetsWorker, **{
                        "id1": _id1,
                        "id2": _id2,
                        "page": (i+1),
                        "inverted": inverted,
                        "videogame": videogame
                    })
                    workers.append(worker)
                    self.threadpool.start(worker)

            Worker.wait_for_all(workers)

            for worker in workers:
                if worker.result is not None:
                    recentSets.extend(worker.result)

            byId = {_set.get("id"): _set for _set in recentSets}
            recentSets = list(byId.values())
            recentSets.sort(key=lambda s: s.get("timestamp"), reverse=True)
            logger.info("Recent sets size: " + str(len(recentSets)))
            callback.emit({"sets": recentSets, "request_time": requestTime})
        except Exception as e:
            logger.error(traceback.format_exc())
            callback.emit({"sets": [], "request_time": requestTime})

    def GetRecentSetsWorker(self, id1, id2, page, videogame, inverted, progress_callback, cancel_event):
        try:
            recentSets = []

            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    # "operationName": "RecentSetsQuery",
                    "variables": {
                        "pid1": id1[0],
                        "uid1": id1[1],
                        "pid2": id2[0],
                        "uid2": id2[1],
                        "page": page,
                        "videogameId": videogame
                    },
                    "query": StartGGDataProvider.RecentSetsQuery
                }
            )

            events = deep_get(data, "data.user.events.nodes", [])

            for event in events:
                if not event:
                    continue
                if not event.get("sets"):
                    continue

                sets = deep_get(event, "sets.nodes")

                for _set in sets:
                    phaseName = ""
                    phaseIdentifier = ""

                    # This is because a display identifier at a major (Ex. Pools C12) will return C12,
                    # otherwise startgg will just return a string containing "1"
                    if deep_get(_set, "phaseGroup.displayIdentifier") != "1":
                        phaseIdentifier = deep_get(
                            _set, "phaseGroup.displayIdentifier")
                    phaseName = deep_get(_set, "phaseGroup.phase.name")

                    p1id = _set.get("slots", [{}, {}])[0].get("entrant", {}).get(
                        "participants", [{}])[0].get("player", {}).get("id")
                    p2id = _set.get("slots", [{}, {}])[1].get("entrant", {}).get(
                        "participants", [{}])[0].get("player", {}).get("id")

                    p1id = str(p1id)
                    p2id = str(p2id)

                    if not p1id in [id1[0], id2[0]] or not p2id in [id1[0], id2[0]]:
                        continue

                    if _set.get("entrant1Score") == -1 or _set.get("entrant2Score") == -1:
                        continue

                    playerToEntrant = {}

                    playerToEntrant[id1[0]] = str(_set.get("slots", [{}])[
                        0].get("entrant", {}).get("id"))
                    playerToEntrant[id2[0]] = str(_set.get("slots", [{}])[
                        1].get("entrant", {}).get("id"))

                    winner = 0

                    winner = 0 if str(_set.get("winnerId")
                                      ) == playerToEntrant[p1id] else 1

                    score = [0, 0]

                    if _set.get("entrant1Score") != None and _set.get("entrant2Score") != None:
                        if p1id == id1[0]:
                            score = [_set.get("entrant1Score") if _set.get("entrant1Score") is not None else "0",
                                     _set.get("entrant2Score") if _set.get("entrant2Score") is not None else "0"]
                        else:
                            score = [_set.get("entrant2Score") if _set.get("entrant2Score") is not None else "0",
                                     _set.get("entrant1Score") if _set.get("entrant1Score") is not None else "0"]
                    else:
                        if (p1id == id1[0] and winner == 0) or (p1id == id1[1] and winner == 1):
                            score = ["W", "L"]
                        else:
                            score = ["L", "W"]

                    if inverted:
                        score.reverse()
                        if winner == 1:
                            winner = 0
                        elif winner == 0:
                            winner = 1

                    entry = {
                        "id": _set.get("id"),
                        "tournament": deep_get(event, "tournament.name"),
                        "event": event.get("name"),
                        "online": event.get("isOnline"),
                        "score": score,
                        "timestamp": event.get("startAt"),
                        "winner": winner,
                        "round": StartGGDataProvider.TranslateRoundName(_set.get("fullRoundText")),
                        "phase_name": phaseName,
                        "phase_id": phaseIdentifier
                    }
                    recentSets.append(entry)
            return recentSets
        except Exception as e:
            logger.error(traceback.format_exc())
            return []

    def GetEntrantsWorker(self, eventSlug, progress_callback, cancel_event):
        from ..Helpers.AutonaStatusManager import AutonaStatusManager
        status_token = AutonaStatusManager.push("Start.gg から参加者情報を取得中...")
        try:
            page = 1
            totalPages = 1
            # final_data = QStandardItemModel()
            players = []
            # Reset top_entrants cache at the start of a fresh fetch
            self.top_entrants = []
            # DB tags of every participant, handed to AutonaPlayerDB at
            # the end so completers only offer this tournament's players.
            # The previous tournament's set stays active until then.
            entrant_tags = set()

            while page <= totalPages:
                logger.info(str(page) + "/" + str(totalPages))
                AutonaStatusManager.update(
                    status_token,
                    f"Start.gg から参加者情報を取得中... ({page}/{totalPages})",
                    progress=page - 1, total=totalPages)
                data = self.QueryRequests(
                    "https://www.start.gg/api/-/gql",
                    type=requests.post,
                    jsonParams={
                        "operationName": "EventEntrantsListQuery",
                        "variables": {
                            "eventSlug": eventSlug,
                            "page": page,
                        },
                        "query": StartGGDataProvider.EntrantsQuery
                    }
                )

                totalPages = deep_get(
                    data, "data.event.entrants.pageInfo.totalPages", 0)

                entrants = deep_get(data, "data.event.entrants.nodes", [])
                logger.info("Entrants: " + str(len(entrants)))

                for i, team in enumerate(entrants):
                    seed = team.get("initialSeedNum", 0)
                    participants = team.get("participants", [])
                    for j, entrant in enumerate(participants):
                        playerData = StartGGDataProvider.ProcessEntrantData(
                            entrant)
                        playerData["seed"] = seed
                        self.player_seeds[playerData["id"]
                                          [0]] = playerData["seed"]
                        players.append(playerData)
                        entrant_tags.add(AutonaPlayerDB.PlayerTag(playerData))
                    # Capture entry-level info for stream title generation
                    # (one record per team/entrant, using first participant's
                    # gamerTag without sponsor prefix)
                    if participants:
                        first = StartGGDataProvider.ProcessEntrantData(participants[0])
                        gamerTag = first.get("gamerTag") or first.get("name") or ""
                        self.top_entrants.append({
                            "seed": seed if seed else 999999,
                            "gamerTag": gamerTag
                        })

                # Defer SaveDB/SetupModel per page — each call is O(full DB)
                # and rebuilds the QStandardItemModel on the GUI thread. We
                # flush once after all pages are done below.
                AutonaPlayerDB.AddPlayers(players, deferRefresh=True)
                players = []

                page += 1

            # Persist + rebuild the model once for the whole fetch. The
            # entrant set is installed first so the rebuilt completer
            # proxy filters correctly from the start.
            AutonaPlayerDB.SaveDB()
            AutonaPlayerDB.SetTournamentPlayers(entrant_tags)
            AutonaPlayerDB.SetupModel()

            # Sort by seed (ascending); unseeded entries fall to the bottom
            self.top_entrants.sort(key=lambda e: e["seed"])

            try:
                self.tshTdp.signals.entrants_updated.emit()
            except Exception:
                logger.error(traceback.format_exc())
        except Exception as e:
            logger.error(traceback.format_exc())
        finally:
            AutonaStatusManager.pop(status_token)

    def ProcessEntrantData(entrant, setData=[]):
        player = entrant.get("player")
        user = entrant.get("user")

        playerData = {}

        if player:
            playerData["prefix"] = player.get("prefix")
            playerData["gamerTag"] = player.get("gamerTag")
            playerData["name"] = player.get("name")

            # Main character
            playerSelections = Counter()

            sets = []

            if not setData:
                sets = deep_get(player, "sets.nodes", [])
            else:
                sets = setData

            playerId = player.get("id")

            if len(sets) > 0:
                for _set in sets:
                    games = _set.get("games", [])
                    if games and len(games) > 0:
                        for game in games:
                            selections = game.get("selections", [])
                            if selections:
                                for selection in selections:
                                    participants = selection.get(
                                        "entrant", {}).get("participants", [])
                                    if len(participants) > 0:
                                        participantId = participants[0].get(
                                            "player", {}).get("id", None)
                                        if participantId and participantId == playerId:
                                            playerSelections[selection.get(
                                                "selectionValue")] += 1

            mains = playerSelections.most_common()

            if len(mains) > 0:
                playerData["startggMains"] = mains

        if user:
            playerData["id"] = [
                player.get("id"),
                user.get("id")
            ]

            if user.get("authorizations"):
                if len(user.get("authorizations", [])) > 0:
                    playerData["twitter"] = user.get("authorizations", [])[
                        0].get("externalUsername")

            if len(user.get("images", [])) > 0:
                playerData["avatar"] = user.get("images")[
                    0].get("url")

            if user.get("location"):
                # Country to country code
                if user.get("location").get("country"):
                    for country in AutonaCountryHelper.countries.values():
                        if user.get("location").get("country") == country.get("en_name"):
                            playerData["country_code"] = country.get(
                                "code")
                            break

                # State — same table-backed resolution as above. This call
                # site never published "city", so it still doesn't.
                state_raw = user.get("location").get("state")
                city_raw = user.get("location").get("city")
                country = playerData.get("country_code")
                if state_raw:
                    resolved = AutonaCountryHelper.FindState(
                        country, state_raw) or (
                        AutonaCountryHelper.FindState(country, city_raw)
                        if city_raw else None)
                    playerData["state_code"] = resolved or state_raw
                elif city_raw and country:
                    resolved = AutonaCountryHelper.FindState(country, city_raw)
                    if resolved:
                        playerData["state_code"] = resolved

            if playerData.get("startggMains"):
                if AutonaGameAssetManager.instance.selectedGame:
                    gameCodename = AutonaGameAssetManager.instance.selectedGame.get(
                        "codename")

                    mains = []

                    for sggmain in playerData.get("startggMains"):
                        main = AutonaGameAssetManager.instance.GetCharacterFromStartGGId(
                            sggmain[0])
                        if main:
                            mains.append([main[0]])

                    playerData["mains"] = {
                        gameCodename: mains
                    }
                else:
                    playerData["mains"] = {}
        if "id" not in playerData:
            playerData["id"] = [
                player.get("id"),
                0
            ]

        return (playerData)

    def GetStandings(self, playerNumber, progress_callback, cancel_event):
        try:
            data = self.QueryRequests(
                "https://www.start.gg/api/-/gql",
                type=requests.post,
                jsonParams={
                    "operationName": "TournamentStandingsQuery",
                    "variables": {
                        "playerNumber": playerNumber,
                        "eventSlug": self.url.split("start.gg/")[1]
                    },
                    "query": StartGGDataProvider.TournamentStandingsQuery
                }

            )

            standings = deep_get(data, "data.event.standings.nodes", [])

            teams = []

            for standing in standings:
                team = {}

                team["placement"] = deep_get(standing, "placement", None)

                participants = deep_get(standing, "entrant.participants")

                if len(participants) > 1:
                    team["name"] = deep_get(standing, "entrant.name")

                team["players"] = []

                for entrant in participants:
                    team["players"].append(StartGGDataProvider.ProcessEntrantData(
                        entrant, deep_get(standing, "entrant.paginatedSets.nodes")))

                teams.append(team)
            return (teams)
        except Exception as e:
            logger.error(traceback.format_exc())

    def GetFutureMatch(self, matchId, progress_callback, cancel_event):
        data = self.QueryRequests(
            "https://www.start.gg/api/-/gql",
            type=requests.post,
            jsonParams={
                "operationName": "FutureSetQuery",
                "variables": {
                    "id": matchId
                },
                "query": StartGGDataProvider.FutureSetQuery
            }
        )

        data = deep_get(data, "data.set", None)

        if not data:
            return {}

        data = self.ProcessFutureSet(data, self.url.split("start.gg/")[1])

        return data

    def GetMatchAndInsertInList(self, setId, list, i, progress_callback, cancel_event):
        set = self.GetFutureMatch(setId, None)

        if set:
            list[i] = set

    def GetFutureMatchesList(self, setsId, progress_callback, cancel_event):
        sets = []
        pool = self.getStationMatchesThreadPool
        i = 0
        workers = []
        for set in setsId:
            sets.append(None)
            worker = Worker(self.GetMatchAndInsertInList, **{
                "setId": set.get("id"),
                "list": sets,
                "i": i
            })
            workers.append(worker)
            pool.start(worker)

            i += 1

        Worker.wait_for_all(workers, self._request_timeout_secs)

        sets_ = {}
        for index, set in enumerate(sets):
            sets_[str(index + 1)] = set

        return sets_

    def GetRealEventURL(self, url):
        matches = re.match(
                ".*start.gg/admin/tournament/[^/]*/brackets/([^/]*)", url)
        if matches: 
            try:
                data = self.QueryRequests(
                        "https://www.start.gg/api/-/gql",
                        type=requests.post,
                        jsonParams={
                            "operationName": "TournamentSlugQuery",
                            "variables": {
                                "id": matches.group(1)
                                },
                            "query": StartGGDataProvider.TournamentSlugQuery
                            }
                        )

                slug = deep_get(data, "data.event.slug", None)
                url = "https://www.start.gg/" + slug
            except:
                logger.error(traceback.format_exc())
        self.url = url
        return url
