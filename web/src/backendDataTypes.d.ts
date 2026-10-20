export interface AutonaCountryInfo {
    code: string;
    display_name: string;
    en_name: string;
}

export interface AutonaPlayerInfo {
    id?: [number, number];
    city?: string;
    state?: AutonaCountryInfo;
    country?: AutonaCountryInfo;
    online_avatar?: string;
    name: string;
    mergedName: string;
    mergedOnlyName: string;
    real_name: string;
    seed: number;
    sponsor_logo?: string;
    team?: string;
    twitter?: string;
    character: AutonaCharacterSelections;
}

export interface AutonaTeamInfo {
    score: number;
    teamName: string;
    player: Record<number, AutonaPlayerInfo>;
    losers: boolean;
    color: string;
}

export interface AutonaScoreInfo {
    best_of: number;
    best_of_text: string;
    match: string;
    phase: string;
    set_id: number;
    station?: string;
    team: Record<number, AutonaTeamInfo>;
}

export interface AutonaCharacterBase {
    codename: string;
    display_name: string;
    en_name: string;
    name: string;
}

export interface AutonaCharacterSelection extends AutonaCharacterBase {
    skin: number; // This is -1 if unset.
}

export type AutonaCharacterSelections = Record<number, AutonaCharacterSelection>;

export interface AutonaCharacterDb {
    [codename: string]: AutonaCharacterDbEntry;
}

export interface AutonaCharacterDbEntry extends AutonaCharacterBase {
    skins: AutonaCharacterSkin[];
}

export interface AutonaCharacterSkin {
    assets: AutonaCharacterSkinAssets;
}

export interface AutonaCharacterSkinAssets {
    art?: AutonaCharacterSkinAsset;
    "base_files/icon"?: AutonaCharacterSkinAsset;
    costume?: AutonaCharacterSkinAsset;
    css?: AutonaCharacterSkinAsset;
    full?: AutonaCharacterSkinAsset;
    profile?: AutonaCharacterSkinAsset;
}

export interface AutonaCharacterSkinAsset {
    asset: string; // Path to the asset
    average_size?: Point2D;
    image_size?: Point2D;
    rescaling_factor?: number;
    type?: string[];
    uncropped_edge?: string[];
}

export interface Point2D {
    x: number;
    y: number;
}

export type AutonaCharacters = Record<string, AutonaCharacterSelection>;

export interface AutonaSetEntrant {
    gamerTag: string;
    prefix?: string;
    name?: string;
    id: number[];
}

export interface AutonaSet {
    bracket_type: string;
    entrants: [AutonaSetEntrant, AutonaSetEntrant];
    id: number;
    isOnline?: boolean;
    isPools: boolean;
    p1_name: string;
    p1_seed?: number;
    p2_name: string;
    p2_seed?: number;
    round: string;
    round_name: string;
    station?: string;
    stream?: string;
    team1score: number;
    team2score: number;
    tournament_phase?: string;
}

export interface AutonaPlayerDbEntry {
    country_code: string;
    custom_textbox: string;
    prefixed_tag: string;
    gamerTag: string;
    mains?: AutonaMainsMap;
    name: string;
    prefix: string;
    twitter: string;
}

export type AutonaCountryCode = string;

export interface AutonaCountryDb {
    [country_code: AutonaCountryCode]: AutonaCountryInfo
}

export type AutonaMainsMap = Record<string, AutonaMain[]>;

export type AutonaMain = [string, number, string];

export type AutonaPlayerDb = Record<string, AutonaPlayerDbEntry>;

export interface AutonaState {
    score: {
        [scoreboard: number]: AutonaScoreInfo;
        ruleset: object;
    };
    game?: {
        name: string;
        smashgg_id: number;
        logo?: string;
        codename?: string;
    };
    tournamentInfo: {
        tournamentName?: string;
        address?: string;
        eventName?: string;
        shortLink?: string;
        endAt?: string;
        startAt?: string;
        eventEndAt?: string;
        eventStartAt?: string;
        initial_load?: boolean;
        numEntrants?: number;
    };
    /** Broadcast-scoped settings, owned by the 配信設定 panel. */
    stream?: {
        /** 配信先: the broadcast channel URL. */
        url?: string;
        /** 配信タイトル. */
        title?: string;
        /** mtime cache-bust versions for /layout/stream_org_*.png (0 = unregistered). */
        orgIconVersion?: number;
        orgThumbVersion?: number;
    };
}

export interface AutonaGamesDb {
    [codename: string]: AutonaGameInfo
}

export interface AutonaGameInfo{
    challonge_game_id: number,
    has_stages: boolean
    has_variants: boolean
    locale: null | object
    name: string
    smashgg_game_id: number
    codename: string // This one isn't in the backend responses, I add it from the key.
}

export interface Delta {
    action: DeltaOpType;
    path: (string | number)[];
    type: string;
    value: any;
}

export type DeltaOpType =
     "type_changes"
   | "values_changed"
   | "dictionary_item_added"
   | "dictionary_item_removed"
   | "iterable_item_added"
   | "iterable_item_removed"
   | "attribute_added"
   | "attribute_removed"
   | "set_item_added"
   | "set_item_removed"
   | "repetition_change";
