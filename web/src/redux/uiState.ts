import {createSlice, PayloadAction} from "@reduxjs/toolkit";


export interface VisibilityState {
    twitter: boolean;
    seed: boolean;
    location: boolean;
    characters: boolean;
    pronouns: boolean;
    additional: boolean;
}

export const visibilitySlice = createSlice({
    name: 'visibility',
    initialState: {
        twitter: true,
        seed: true,
        location: false,
        characters: false,
        pronouns: false,
        additional: true,
    } as VisibilityState,
    reducers: {
        toggle(state, action: PayloadAction<keyof VisibilityState>) {
            state[action.payload] = !state[action.payload];
        }
    },
});
