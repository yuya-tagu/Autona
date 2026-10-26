"""Start.gg bracket data model (no UI).

The old Bracket dock rendering (AutonaBracketView / AutonaBracketWidget)
was removed with the Top 8 rework; this module stays as the pure
topology/propagation model consumed by AutonaAnalysisWidget (multi phase
group table) and core.Top8Model.FromBracket (Top 8 extraction, which
core.Top8Service runs in either shell).

Core since ADR-005 S3e-8 (was src/AutonaBracket.py). The one thing it read
from the Qt side, the localized round names GetRoundName falls back to
(self.matchNames), is handed in by whoever builds the bracket
(`matchNames=`); the Top 8 import never asks for a round name.

Import side effects: none (agent-pitfalls #1). Qt-free, like everything under
``src/core``.
"""
from loguru import logger


class BracketSet():
    BYE = -1
    PENDING = -2

    def __init__(self, bracket: "Bracket", pos) -> None:
        self.bracket: "Bracket" = bracket
        self.playerIds = [BracketSet.BYE, BracketSet.BYE]
        self.score = [0, 0]
        self.winNext: "BracketSet" = None
        self.winNextSlot: int = 0
        self.loseNext: "BracketSet" = None
        self.loseNextSlot: int = 0
        self.pos = pos  # [roundNum:int, indexInRound:int]
        self.finished = False
        self.setId = None
        self.identifier = None
        self.fullRoundText = None
        # If any slot is prereqType == "bye", the set is a pre-resolved bye
        # that should not be rendered.
        self.isByeSet = False


class Bracket():
    """Start.gg driven bracket.

    Unlike the old implementation, this class does not pre-build a power-of-two
    tournament topology. It consumes the rounds/slots structure returned by
    Start.gg verbatim:

        sets = {
            "1":  [ {id, slots:[{prereqType, prereqId, prereqPlacement, seedNum, entrantId}, ...], score, finished, ...}, ... ],
            "2":  [ ... ],
            ...
            "-1": [ ... ],
            ...
        }

    Round keys follow Start.gg's convention: positive = winners, negative =
    losers, max(positive) = grand final, max(positive)+1 = GF reset.
    """

    def __init__(self, phaseGroupData=None, matchNames=None):
        # The localized round names GetRoundName falls back to (the
        # "match" terms of the round-name tables, AutonaLocaleHelper
        # .matchNames on the Qt side). None: no names -- GetRoundName then
        # answers as it did with the tables not loaded yet.
        self.matchNames = matchNames if matchNames is not None else {}
        self.rounds = {}  # roundKey(str) -> list[BracketSet]
        self.setsById = {}  # Start.gg set id -> BracketSet
        self.entrantCount = 0
        self.originalPlayerNumber = 0

        # Progression flags are no longer auto-applied to bracket topology.
        # They are kept as attributes for downstream state compatibility only.
        self.progressionsIn = 0
        self.progressionsOut = 0
        self.winnersOnlyProgressions = True
        self.customSeeding = False

        if phaseGroupData is not None:
            self.LoadFromStartGg(phaseGroupData)

    def LoadFromStartGg(self, phaseGroupData: dict):
        self.rounds = {}
        self.setsById = {}

        entrants = phaseGroupData.get("entrants") or []
        self.entrantCount = len(entrants)
        self.originalPlayerNumber = self.entrantCount

        setsByRound = phaseGroupData.get("sets") or {}

        # Pass 1: create BracketSet instances preserving Start.gg's set order
        # within each round. pos[1] is the display row; sets earlier in the
        # list sit higher on screen.
        for roundKey, rawSets in setsByRound.items():
            try:
                roundNum = int(roundKey)
            except (TypeError, ValueError):
                logger.warning(f"Bracket: skipping non-numeric round key {roundKey!r}")
                continue

            bracketSets = []
            for idx, raw in enumerate(rawSets):
                bs = BracketSet(self, [roundNum, idx])
                # Normalize to string so prereqId lookups don't fail on
                # int-vs-string mismatches (preview sets use "preview_..." IDs
                # while live sets use int IDs).
                rawId = raw.get("id")
                bs.setId = str(rawId) if rawId is not None else None
                bs.identifier = raw.get("identifier")
                bs.fullRoundText = raw.get("fullRoundText")

                score = raw.get("score") or [0, 0]
                bs.score = [
                    score[0] if score[0] is not None else 0,
                    score[1] if score[1] is not None else 0,
                ]
                bs.finished = bool(raw.get("finished"))

                slots = raw.get("slots") or []
                hasBye = False
                for i in range(min(2, len(slots))):
                    slot = slots[i] or {}
                    prereqType = slot.get("prereqType")
                    seedNum = slot.get("seedNum")
                    if prereqType == "bye":
                        hasBye = True
                        bs.playerIds[i] = BracketSet.BYE
                    elif seedNum is not None:
                        bs.playerIds[i] = int(seedNum)
                    else:
                        # Unresolved — will be filled by UpdateBracket()
                        bs.playerIds[i] = BracketSet.PENDING

                bs.isByeSet = hasBye

                bracketSets.append(bs)
                if bs.setId is not None:
                    self.setsById[bs.setId] = bs

            self.rounds[roundKey] = bracketSets

        # Pass 2: wire winNext / loseNext using slot prereq references.
        # For each set S, each of its slots may declare: "the winner (or loser)
        # of prereq set P goes here". So we walk S's slots and set P.winNext/P.loseNext = S.
        wiredWin = 0
        wiredLose = 0
        for roundKey, bracketSets in self.rounds.items():
            for bs in bracketSets:
                raw = self._findRawSet(setsByRound, roundKey, bs.pos[1])
                if raw is None:
                    continue
                slots = raw.get("slots") or []
                for slotIdx, slot in enumerate(slots[:2]):
                    slot = slot or {}
                    if slot.get("prereqType") != "set":
                        continue
                    prereqId = slot.get("prereqId")
                    prereqPlacement = slot.get("prereqPlacement")
                    if prereqId is None:
                        continue
                    prereqSet = self.setsById.get(str(prereqId))
                    if prereqSet is None:
                        continue
                    if prereqPlacement == 1:
                        prereqSet.winNext = bs
                        prereqSet.winNextSlot = slotIdx
                        wiredWin += 1
                    elif prereqPlacement == 2:
                        prereqSet.loseNext = bs
                        prereqSet.loseNextSlot = slotIdx
                        wiredLose += 1

        logger.info(
            f"Bracket wired: entrants={self.entrantCount}, "
            f"rounds={len(self.rounds)}, sets={sum(len(r) for r in self.rounds.values())}, "
            f"winNext={wiredWin}, loseNext={wiredLose}"
        )

        # Resolve pre-bye auto-advances so WR1 players who faced a bye show
        # up in WR2 etc.
        self.UpdateBracket()

        # Post-propagation diagnostic: report how many player slots resolved
        # in each round so losers-side gaps are obvious from the log.
        for rk in sorted(self.rounds.keys(), key=lambda k: (int(k) < 0, abs(int(k)))):
            sets = self.rounds[rk]
            resolved = sum(1 for s in sets for p in s.playerIds if p > 0)
            total = len(sets) * 2
            logger.info(f"  round {rk}: {len(sets)} sets, resolved={resolved}/{total}")

    @staticmethod
    def _findRawSet(setsByRound, roundKey, idx):
        rawList = setsByRound.get(roundKey)
        if not rawList or idx >= len(rawList):
            return None
        return rawList[idx]

    def IsBye(self, playerId):
        return playerId == BracketSet.BYE

    def UpdateBracket(self):
        """Propagate resolved player IDs forward through winNext/loseNext.

        Processes rounds in ascending distance from the seeds: WR1, WR2, ...
        then LR1, LR2, ... Grand final and reset are positive rounds so they
        fall naturally into the winners pass.
        """
        orderedKeys = sorted(
            self.rounds.keys(),
            key=lambda k: (int(k) < 0, abs(int(k)))
        )

        for roundKey in orderedKeys:
            for bs in self.rounds[roundKey]:
                self._propagate(bs)

    def _propagate(self, bs: BracketSet):
        if bs.winNext is None and bs.loseNext is None:
            return

        p0, p1 = bs.playerIds

        bye0 = self.IsBye(p0)
        bye1 = self.IsBye(p1)

        # Bye cases — advance the non-bye side; bye propagates as bye.
        if bye0 and bye1:
            return
        if bye1 and not bye0:
            self._assignNext(bs.winNext, bs.winNextSlot, p0)
            if bs.loseNext is not None:
                self._assignNext(bs.loseNext, bs.loseNextSlot, BracketSet.BYE)
            return
        if bye0 and not bye1:
            self._assignNext(bs.winNext, bs.winNextSlot, p1)
            if bs.loseNext is not None:
                self._assignNext(bs.loseNext, bs.loseNextSlot, BracketSet.BYE)
            return

        # No byes — use score if finished, else forward pending.
        if bs.finished and (bs.score[0] != bs.score[1]):
            won, lost = (0, 1) if bs.score[0] > bs.score[1] else (1, 0)
            self._assignNext(bs.winNext, bs.winNextSlot, bs.playerIds[won])
            if bs.loseNext is not None:
                self._assignNext(bs.loseNext, bs.loseNextSlot, bs.playerIds[lost])
        else:
            self._assignNext(bs.winNext, bs.winNextSlot, BracketSet.PENDING)
            if bs.loseNext is not None:
                self._assignNext(bs.loseNext, bs.loseNextSlot, BracketSet.PENDING)

    @staticmethod
    def _assignNext(target: BracketSet, slot: int, value: int):
        if target is None:
            return
        # Don't overwrite an already-resolved seed/entrant slot with PENDING.
        current = target.playerIds[slot]
        if value == BracketSet.PENDING and current != BracketSet.PENDING and current != BracketSet.BYE:
            return
        target.playerIds[slot] = value

    def GetRoundName(self, round: str, winnersCutout=[0, 0], losersCutout=[0, 0], displayIdx=None):
        """Derive a localized round name from the round key.

        Grand final / final naming only applies when the bracket ends with the
        classic single-set rounds (GF + optional Reset). Pools that feed into
        another phase never have those rounds, so we fall back to plain
        "Winners Round N" / "Losers Round N" labels.

        When `displayIdx` is provided, the numeric label uses it instead of
        the raw round number. This keeps round labels contiguous from 1 even
        when Start.gg returns non-contiguous round keys (e.g. a progression
        bracket whose first losers rounds are omitted).
        """
        try:
            roundNumber = int(round)
        except (TypeError, ValueError):
            return ""

        # Prefer the authoritative label from Start.gg when we have one
        bracketSets = self.rounds.get(str(roundNumber)) or []
        for bs in bracketSets:
            if bs.fullRoundText:
                return bs.fullRoundText

        positiveRounds = sorted(
            int(r) for r in self.rounds.keys() if int(r) > 0)
        negativeRounds = sorted(
            (int(r) for r in self.rounds.keys() if int(r) < 0), reverse=True)

        if not positiveRounds and not negativeRounds:
            return ""

        def setCount(r):
            return len(self.rounds.get(str(r)) or [])

        maxPos = positiveRounds[-1] if positiveRounds else 0
        # GF Reset is present when the two highest positive rounds both have
        # exactly one set. If only the highest has one set, that one is GF.
        hasGfReset = (
            len(positiveRounds) >= 2
            and setCount(maxPos) == 1
            and setCount(maxPos - 1) == 1
        )
        hasGf = setCount(maxPos) == 1 if positiveRounds else False

        # Winners "final" meaningful only when there's a real GF above it
        gfsRound = (maxPos - 1) if hasGfReset else (maxPos if hasGf else None)

        lastLosers = negativeRounds[0] if negativeRounds else 0
        # Losers final meaningful only when its winner feeds into GF
        hasLosersFinal = gfsRound is not None and setCount(lastLosers) == 1

        if roundNumber > 0:
            if hasGfReset and roundNumber == maxPos:
                return self.matchNames.get("grand_final_reset")
            if gfsRound is not None and roundNumber == gfsRound:
                return self.matchNames.get("grand_final")
            if gfsRound is not None and roundNumber == gfsRound - 1:
                return self.matchNames.get("winners_final")
            if gfsRound is not None and roundNumber == gfsRound - 2:
                return self.matchNames.get("winners_semi_final")
            if gfsRound is not None and roundNumber == gfsRound - 3:
                return self.matchNames.get("winners_quarter_final")
            num = displayIdx if displayIdx is not None else roundNumber
            return self.matchNames.get("winners_round").format(num)

        if hasLosersFinal and roundNumber == lastLosers:
            return self.matchNames.get("losers_final")
        if hasLosersFinal and roundNumber == lastLosers + 1:
            return self.matchNames.get("losers_semi_final")
        if hasLosersFinal and roundNumber == lastLosers + 2:
            return self.matchNames.get("losers_quarter_final")
        num = displayIdx if displayIdx is not None else abs(roundNumber)
        return self.matchNames.get("losers_round").format(num)
