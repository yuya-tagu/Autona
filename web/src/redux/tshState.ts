import {Action, createSlice, PayloadAction, WritableDraft} from '@reduxjs/toolkit'
import {applyDeltas, combineDeltas} from "../stateDelta";
import websocketConnection from "../websocketConnection";
import {
    TSHCountryDb,
    TSHCharacterDb,
    TSHCharacterDbEntry,
    TSHPlayerDb,
    TSHState,
    TSHGamesDb, Delta
} from "../backendDataTypes";

export type ReceivedDeltas = {
    // Canonical name per Design.md §3.2.3 R-Sync-6.
    revision?: number;
    // Legacy alias kept while older payloads are still in flight.
    delta_index?: number;
    delta: Delta[];
};

export type TSHStateMessage = {
    state: TSHState;
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

export type TshStateReduxState = {
    tshState: TSHState;
    stateDeltas: ReceivedDeltas[];
    maxAppliedDeltaIdx: number;
    initializing: boolean;
};

export const tshStateSlice = createSlice({
    name: 'tshState',
    initialState: {
        tshState: {} as TSHState,
        stateDeltas: [],
        maxAppliedDeltaIdx: -1,
        initializing: true,
    } as TshStateReduxState,
    reducers: {
        applySavedDeltas(state: WritableDraft<TshStateReduxState>, action: Action) {
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
                let expected = state.maxAppliedDeltaIdx + 1;
                let gap = false;
                for (const d of sortedDeltas) {
                    if (getRevision(d) !== expected) {
                        gap = true;
                        break;
                    }
                    expected += 1;
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
                    console.log("Applying deltas: ", combineDeltas(deltaSet.delta));
                    applyDeltas(state.tshState, deltaSet.delta);
                }
                state.maxAppliedDeltaIdx = getRevision(sortedDeltas[sortedDeltas.length-1]);
            }

            state.initializing = false;
            state.stateDeltas = [];
        },

        addDeltas(state: TshStateReduxState, action: PayloadAction<ReceivedDeltas>){
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

        overwrite(state: TshStateReduxState, action: PayloadAction<TSHStateMessage>) {
            state.tshState = action.payload.state;
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

export const tshPlayersSlice = createSlice({
    name: 'tshPlayers',
    initialState: {
        players: {} as TSHPlayerDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<TSHPlayerDb>) {
            state.players = action.payload;
            for (let k in state.players) {
                state.players[k].prefixed_tag = k;
            }
            state.initializing = false;
        }
    }
});

export const tshCharactersSlice = createSlice({
    name: 'tshCharacters',
    initialState: {
        characters: {} as TSHCharacterDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<TSHCharacterDb>) {
            // We need our character list to be keyed by the en_name, because things like player mains are set
            // to the english name instead the localized name. We won't be able to do lookups if we don't
            // rearrange it like this.
            const enChars: TSHCharacterDb = {};
            Object.values(action.payload).forEach((char: TSHCharacterDbEntry) => {
                enChars[char.en_name] = char;
            });
            console.log("Character data set", enChars);
            state.characters = enChars;
            state.initializing = false;
        }
    }
});

export const tshGamesSlice = createSlice({
    name: 'tshGames',
    initialState: {
        value: {} as TSHGamesDb,
        initializing: true
    },
    reducers: {
        overwrite(state, action: PayloadAction<TSHGamesDb>) {
            state.value = action.payload;
            for (let k in state.value) {
                state.value[k].codename = k;
            }
            state.initializing = false;
        }
    }
});

export const tshCountriesSlice = createSlice({
    name: 'tshCountries',
    initialState: {
        value: {} as TSHCountryDb,
        initializing: true,
    },
    reducers: {
        overwrite(state, action: PayloadAction<TSHCountryDb>) {
            state.value = action.payload;
            state.initializing = false;
        }
    }
});
