import {configureStore, combineReducers, Action} from '@reduxjs/toolkit'
import {
    autonaCharactersSlice,
    autonaCountriesSlice,
    autonaGamesSlice,
    autonaPlayersSlice,
    autonaStateSlice,
    websocketInfoSlice
} from './autonaState';
import {produce} from "immer";

export const autonaStore = configureStore({
    reducer: combineReducers({
        autonaState: autonaStateSlice.reducer,
        autonaPlayers: autonaPlayersSlice.reducer,
        autonaCharacters: autonaCharactersSlice.reducer,
        autonaGames: autonaGamesSlice.reducer,
        autonaCountries: autonaCountriesSlice.reducer,
        websocketInfo: websocketInfoSlice.reducer,
    }),
    devTools: {
        // Helps keep the devtools crispy.
        stateSanitizer: function<S>(state: S, index) {
            return produce(state, (ds: any) => {
                try {
                    ds.autonaPlayers = `<${objLen(ds.autonaPlayers.players)} players>`;
                    ds.autonaCountries = `<${objLen(ds.autonaCountries.value)} countries>`;
                    for (let k in ds.autonaCharacters.characters) {
                        ds.autonaCharacters.characters[k].skins = `<${objLen(ds.autonaCharacters.characters[k].skins)} skins>`
                    }
                    ds.autonaState.autonaState.top8 = "omitted";
                    ds.autonaState.autonaState.notes = "omitted";
                } catch {}
            });
        },
        predicate: function<S, A extends Action>(state: S, action: A) {
            return action.type !== 'autonaState/maybeApplySavedDeltas';
        }
    },
});

const objLen = (o: any) => Object.keys(o).length;

export type AutonaStore = typeof autonaStore;
export type ReduxState = ReturnType<AutonaStore['getState']>
export type ReduxDispatch = AutonaStore['dispatch'];
