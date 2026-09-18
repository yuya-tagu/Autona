export const BACKEND_PORT = process.env.NODE_ENV === 'production'
    ? window.location.port
    // Dev-server fallback; keep in sync with DEFAULT_WEBSERVER_PORT
    // (src/core/SettingsManager.py).
    : 5107;

export const BASE_URL = `http://${window.location.hostname}:${BACKEND_PORT}`


export const inlineFlagWidth = 24;
