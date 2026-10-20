import {Action, createSlice, PayloadAction, WritableDraft} from '@reduxjs/toolkit'
import {applyDeltas, combineDeltas} from "../stateDelta";
import websocketConnection from "../websocketConnection";
import {
    AutonaCountryDb,
    AutonaCharacterDb,
    AutonaCharacterDbEntry,
    AutonaPlayerDb,
    AutonaState,
    AutonaGamesDb, Delta
} from "../backendDataTypes";

export type ReceivedDeltas = {
    // Canonical name per Design.md §3.2.3 R-Sync-6.
    revision?: number;
    // Legacy alias kept while older payloads are still in flight.
    delta_index?: number;
    // First revision coalesced into this payload. The server merges several
    // state_updated emits into one message, so a payload covers the range
    // [first_revision, revision]. Optional: pre-first_revision payloads
    // fall back to treating the payload as the single revision `revision`.
    first_revision?: number;
    delta: Delta[];
};

export type AutonaStateMessage = {
    state: AutonaState;
    // Canonical name per Design.md §3.2.3 R-Sync-7.
    revision?: number;
    // Legacy alias.
    delta_index?: number;
}

// Pull the revision from a payload, preferring the canonical key.
function getRevision(p: { revision?: number; delta_index?: number }): number {
    if (typeof p.revision === 'number') return p.revision;
    if (typeof p.delta_index === 'number') return p.delta_index;
    return -1;
}

export type WebsocketStatus =
    'initial'
    | 'connected'
    | 'errored'
    | 'disconnected';

export const websocketInfoSlice = createSlice({
    name: 'websocketInfo',
    initialState: {
        status: 'initial' as WebsocketStatus,
        errorMessage: null as (null | string)
    },
    reducers: {
        setStatus(state, action: PayloadAction<WebsocketStatus>) {
            state.status = action.payload;
        }
    }
});

export type AutonaStateReduxState = {
    autonaState: AutonaState;
    stateDeltas: ReceivedDeltas[];
    maxAppliedDeltaIdx: number;
    initializing: boolean;
};

export const autonaStateSlice = createSlice({
    name: 'autonaState',
    initialState: {
        autonaState: {} as AutonaState,
        stateDeltas: [],
        maxAppliedDeltaIdx: -1,
        initializing: true,
    } as AutonaStateReduxState,
    reducers: {
        applySavedDeltas(state: WritableDraft<AutonaStateReduxState>, action: Action) {
            // Sort by revision so out-of-order arrivals get applied in
            // the right sequence (Design.md §3.2.3 R-Sync-6).
            let sortedDeltas = state.stateDeltas
                .toSorted((a, b) => getRevision(a) - getRevision(b));
            const staleDeltas = sortedDeltas.filter(
                (d) => getRevision(d) <= state.maxAppliedDeltaIdx);
            sortedDeltas = sortedDeltas.filter(
                (d) => getRevision(d) > state.maxAppliedDeltaIdx);

            if (staleDeltas.length > 0) {
                console.warn("Skipping applying stale deltas...", staleDeltas);
            }

            if (sortedDeltas.length > 0) {
                // Detect a gap: the surviving deltas must form a contiguous
                // sequence starting from maxAppliedDeltaIdx + 1. If anything
                // is missing we cannot safely apply (state would diverge),
                // so request a full snapshot and drop the queue
                // (Design.md §3.2.3 R-Sync-8).
                // A payload covers the revision range
                // [first_revision, revision]; it is contiguous if that
                // range includes the next expected revision.
                let expected = state.maxAppliedDeltaIdx + 1;
                let gap = false;
                for (const d of sortedDeltas) {
                    const last = getRevision(d);
                    const first = (typeof d.first_revision === 'number' && d.first_revision >= 0)
                        ? d.first_revision
                        : last;
                    if (first > expected || last < expected) {
                        gap = true;
                        break;
                    }
                    expected = last + 1;
                }

                if (gap) {
                    console.warn(
                        "Detected revision gap in delta queue; "
                        + "requesting full snapshot. Queue revisions:",
                        sortedDeltas.map(getRevision),
                        "lastApplied:", state.maxAppliedDeltaIdx,
                    );
                    websocketConnection.instance().emit("program_state", {});
                    state.stateDeltas = [];
                    state.initializing = false;
                    return;
                }

                for (let deltaSet of sortedDeltas) {
                    if (process.env.NODE_ENV !== 'production') {
                        console.log("Applying deltas: ", combineDeltas(deltaSet.delta));
                    }
                    applyDeltas(state.autonaState, deltaSet.delta);
                }
                state.maxAppliedDeltaIdx = getRevision(sortedDeltas[sortedDeltas.length-1]);
            }

            state.initializing = false;
            state.stateDeltas = [];
        },

        addDeltas(state: AutonaStateReduxState, action: PayloadAction<ReceivedDeltas>){
            const rev = getRevision(action.payload);
            // Strictly-less-than handles the duplicate-delta case (revision
            // equal to maxApplied means we already applied it). The gap
            // detection happens in applySavedDeltas.
            if (rev >= 0 && rev < state.maxAppliedDeltaIdx) {
                console.warn("Received out of order delta! Requesting new full state.");
                websocketConnection.instance().emit("program_state", {});
            }

            state.stateDeltas.push(action.payload);
        },

        overwrite(state: AutonaStateReduxState, action: PayloadAction<AutonaStateMessage>) {
            state.autonaState = action.payload.state;
            const rev = getRevision(action.payload);
            // A snapshot is the source of truth for revision, so adopt it
            // (could be lower than maxApplied after server restart).
            state.maxAppliedDeltaIdx = rev;
            state.stateDeltas = [];
            state.initializing = false;
        },

        loadingNewData(state, action) {
            state.initializing = true;
        }
    }
})

export const autonaPlayersSlice = createSlice({
    name: 'autonaPlayers',
    initialState: {
        players: {} as AutonaPlayerDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<AutonaPlayerDb>) {
            state.players = action.payload;
            for (let k in state.players) {
                state.players[k].prefixed_tag = k;
            }
            state.initializing = false;
        }
    }
});

export const autonaCharactersSlice = createSlice({
    name: 'autonaCharacters',
    initialState: {
        characters: {} as AutonaCharacterDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<AutonaCharacterDb>) {
            // We need our character list to be keyed by the en_name, because things like player mains are set
            // to the english name instead the localized name. We won't be able to do lookups if we don't
            // rearrange it like this.
            const enChars: AutonaCharacterDb = {};
            Object.values(action.payload).forEach((char: AutonaCharacterDbEntry) => {
                enChars[char.en_name] = char;
            });
            console.log("Character data set", enChars);
            state.characters = enChars;
            state.initializing = false;
        }
    }
});

export const autonaGamesSlice = createSlice({
    name: 'autonaGames',
    initialState: {
        value: {} as AutonaGamesDb,
        initializing: true
    },
    reducers: {
        overwrite(state, action: PayloadAction<AutonaGamesDb>) {
            state.value = action.payload;
            for (let k in state.value) {
                state.value[k].codename = k;
            }
            state.initializing = false;
        }
    }
});

export const autonaCountriesSlice = createSlice({
    name: 'autonaCountries',
    initialState: {
        value: {} as AutonaCountryDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<AutonaCountryDb>) {
            state.value = action.payload;
            state.initializing = false;
        }
    }
});
