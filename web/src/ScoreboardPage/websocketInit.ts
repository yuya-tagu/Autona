import {
    autonaCharactersSlice,
    autonaCountriesSlice,
    autonaGamesSlice,
    autonaPlayersSlice,
    autonaStateSlice,
    websocketInfoSlice
} from "../redux/autonaState";
import { autonaStore } from "../redux/store";
import socketConnection from "../websocketConnection";
import {BACKEND_PORT} from "../env";

let initialized = false;

export default function websocketInit() {
    if (initialized) {
        return;
    }

    initialized = true;
    const socket = socketConnection.instance();

    socket.on("connect", () => {
        console.log("SocketIO connection established.");
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("connected"));
        socket.emit("playerdb", {}, () => {
            console.log("Autona acked player db request")
        });
        socket.emit("characters", {}, () => {
            console.log("Autona acked characters request")
        });
        socket.emit("games", {}, () => {
            console.log("Autona acked games request")
        });

        loadCountriesFile();
    });

    socket.on("program_state", data => {
        console.log("Autona state received ", data);
        autonaStore.dispatch(autonaStateSlice.actions.overwrite(data));
    });

    socket.on("games", data => {
        console.log("Autona game info received ", data);
        autonaStore.dispatch(autonaGamesSlice.actions.overwrite(data));
    })

    socket.on("countries", data => {
        console.log("Autona countries info received.")
        autonaStore.dispatch(autonaCountriesSlice.actions.overwrite(data));

    });

    socket.on("program_state_update", deltaMessage => {
        if (process.env.NODE_ENV !== 'production') {
            console.log("Autona state update received", deltaMessage);
        }
        autonaStore.dispatch(autonaStateSlice.actions.addDeltas(deltaMessage));
        // Arrival-driven flush (Design.md §2.2 R-React-6): the server
        // already coalesces bursts, so waiting another client-side batch
        // tick only adds latency. Apply immediately on arrival.
        const state = autonaStore.getState();
        if (state.websocketInfo.status === "connected") {
            autonaStore.dispatch(autonaStateSlice.actions.applySavedDeltas());
        }
    });

    socket.on("playerdb", data => {
        console.log("Player data received", data);
        autonaStore.dispatch(autonaPlayersSlice.actions.overwrite(data));
    })

    socket.on("characters", data => {
        console.log("Character data received", data);
        autonaStore.dispatch(autonaCharactersSlice.actions.overwrite(data));
    })

    socket.on("disconnect", () => {
        console.log("SocketIO disconnected.")
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("disconnected"));
        // Per Design.md §3.2.3 R-Sync-9, the server pushes a fresh
        // `program_state` event on every (re)connect, so reconnecting is
        // enough — the resulting overwrite() reducer adopts the new
        // revision and drops any deltas accumulated mid-disconnect.
        socket.connect();
    });

    socket.on('error', (err) => {
        console.log(err);
        autonaStore.dispatch(websocketInfoSlice.actions.setStatus("errored"));
    });

    // This can't be set up twice because of the initialization guard at the top of the function.
    // Design.md §2.2 R-React-6: deltas are applied on arrival (above); this
    // 200ms tick is only a safety net for deltas queued while the socket
    // was not yet "connected" (e.g. reconnect races).
    setInterval(() => {
        const state = autonaStore.getState();
        if (state.websocketInfo.status === "connected" && state.autonaState?.stateDeltas.length > 0) {
            autonaStore.dispatch(autonaStateSlice.actions.applySavedDeltas());
        }
    }, 200);
}

// Todo, maybe move this to a thunk or something.
const loadCountriesFile = () => {
    fetch(
        `http://${window.location.hostname}:${BACKEND_PORT}/assets/data_countries.json`
    ).then(
        (resp) => resp.json()
    ).then((json) => {
        for (let key in json) {
            json[key].code = key;
        }

        console.log("Loaded countries json file", json);
        autonaStore.dispatch(autonaCountriesSlice.actions.overwrite(json));
    }).catch((e) => {
        console.error("Failed to request countries file", e);
        autonaStore.dispatch(autonaCountriesSlice.actions.overwrite({}));
    });
}

