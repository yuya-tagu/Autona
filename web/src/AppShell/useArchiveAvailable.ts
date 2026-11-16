import React from "react";
import {apiGetJson} from "../api";

const RECHECK_MS = 15_000;

// Whether the archive clipper worker is installed (ADR-006 A1): asked when
// the nav mounts, and once more after 15 s when the first answer is "no" (the
// worker probe can be slow on a cold start). Any failure counts as "not
// available", so the item stays hidden.
export function useArchiveAvailable(): boolean {
    const [available, setAvailable] = React.useState(false);
    React.useEffect(() => {
        let live = true;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const ask = (retry: boolean) => {
            apiGetJson<{data?: {available?: boolean}}>("/api/v1/archive/status")
                .then((body) => {
                    if (!live) {
                        return;
                    }
                    const ok = body?.data?.available === true;
                    setAvailable(ok);
                    if (!ok && retry) {
                        timer = setTimeout(() => ask(false), RECHECK_MS);
                    }
                }, () => {
                    if (live && retry) {
                        timer = setTimeout(() => ask(false), RECHECK_MS);
                    }
                });
        };
        ask(true);
        return () => {
            live = false;
            if (timer !== undefined) {
                clearTimeout(timer);
            }
        };
    }, []);
    return available;
}
