import {createFilterOptions} from "@mui/material";
import {AutonaPlayerDbEntry} from "./backendDataTypes";

// A long player DB: the list shows the first matches only.
export const filterPlayers = createFilterOptions<AutonaPlayerDbEntry>({
    limit: 50,
    stringify: (player) => player.prefixed_tag ?? player.gamerTag ?? "",
});

export function playerLabel(player: AutonaPlayerDbEntry | string): string {
    return typeof player === "string"
        ? player
        : (player.prefixed_tag ?? player.gamerTag ?? "");
}
