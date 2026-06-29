import {BindingKey} from "./types";

export const SAMPLE_STATE: any = {
    score: {
        "1": {
            best_of: 5,
            phase: "Top 8",
            match: "Winners Finals",
            team: {
                "1": {
                    score: 2,
                    player: {
                        "1": {
                            name: "PLAYER ONE",
                            team: "AUTONA",
                            seed: 1,
                            twitter: "@player_one",
                            state: {name: "Tokyo"},
                            country: {},
                        },
                    },
                },
                "2": {
                    score: 1,
                    player: {
                        "1": {
                            name: "PLAYER TWO",
                            team: "CHALLENGER",
                            seed: 8,
                            twitter: "@player_two",
                            state: {name: "Osaka"},
                            country: {},
                        },
                    },
                },
            },
        },
    },
    tournamentInfo: {
        tournamentName: "AUTONA CHAMPIONSHIP",
        logoVersion: 0,
    },
};

function team(state: any, side: 1 | 2): any {
    return state?.score?.["1"]?.team?.[String(side)] ?? {};
}

function player(state: any, side: 1 | 2): any {
    return team(state, side)?.player?.["1"] ?? {};
}

export function resolveBinding(binding: BindingKey, state: any): string | number {
    const match = binding.match(/^p([12])\.(.+)$/);
    if (match) {
        const side = Number(match[1]) as 1 | 2;
        const field = match[2];
        const p = player(state, side);
        const t = team(state, side);
        const showSponsor = state?.display_options?.show_sponsor !== false;
        switch (field) {
            case "name": return p.name ?? "";
            case "sponsorName": return showSponsor ? p.team ?? "" : "";
            case "sponsorLogo": return showSponsor
                ? resolveStateAsset(p.sponsor_logo)
                : "";
            case "score": return t.score ?? 0;
            case "countryFlag": return resolveStateAsset(p.country?.asset);
            case "seed": return p.seed ? `SEED ${p.seed}` : "";
            case "twitter": return p.twitter ?? "";
            case "state": return p.state?.name ?? "";
        }
    }
    switch (binding) {
        case "tournament.logo": {
            const version = state?.tournamentInfo?.logoVersion ?? 0;
            return `../logo.png?v=${encodeURIComponent(version)}`;
        }
        case "tournament.name":
            return state?.tournamentInfo?.tournamentName ?? "";
        case "match.phase":
            return state?.score?.["1"]?.phase ?? "";
        case "match.round":
            return state?.score?.["1"]?.match ?? "";
        case "games":
            return state?.score?.["1"]?.best_of ?? 0;
        default:
            return "";
    }
}

export function resolveStateAsset(path: unknown): string {
    if (typeof path !== "string" || !path.trim()) return "";
    if (/^(data:|blob:|https?:|file:)/i.test(path)) return path;
    return `../../${path.replace(/^\.?[\\/]+/, "").replaceAll("\\", "/")}`;
}
