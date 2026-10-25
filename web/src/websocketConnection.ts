import {io, Socket} from "socket.io-client";
import {BACKEND_PORT} from "./env";
import {socketAuth} from "./autonaToken";

export class SocketSingleton {
    _instance: null | Socket = null;

    instance(): Socket {
        if (this._instance == null) {
            this._instance = io(`ws://${window.location.hostname}:${BACKEND_PORT}/`, {
                transports: ['websocket', 'webtransport'],
                timeout: 5000,
                reconnectionDelay: 500,
                reconnectionDelayMax: 1500,
                // The access token when this page was opened with one
                // (ADR-005 S3a): from another computer, a connection without
                // it only reads. Asked for on every (re)connect.
                auth: (cb) => cb(socketAuth()),
            });
        }
        return this._instance;
    }
}

const socketHolder =  new SocketSingleton();

export default socketHolder;