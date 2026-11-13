import {expect, test} from "@playwright/test";
import {io} from "socket.io-client";
import {serverUrl} from "./server";

// What the app is busy with (ADR-005 S-final SF-2), on the headless app: a
// new Socket.IO connection is told the status at once (`status`), and GET
// /api/v1/status answers the same {revision, jobs}. No page shows it yet
// (the AppShell's footer, SF-5a, will). The other specs may run jobs, so
// only the shape is pinned here; its words and schedule are pinned by
// tests/test_status_service.py and tests/test_webserver_delta_debounce.py.

interface Status {
    revision: number;
    jobs: {id: number; code: string; args: object; done: number; total: number}[];
}

function expectStatus(status: Status) {
    expect(typeof status.revision).toBe("number");
    expect(Array.isArray(status.jobs)).toBe(true);
    for (const job of status.jobs) {
        expect(Object.keys(job).sort()).toEqual(
            ["args", "code", "done", "id", "total"]);
    }
}

test("a new connection is told the status, and GET answers it", async ({request}) => {
    const socket = io(serverUrl("/"), {reconnection: false});
    try {
        const told = await new Promise<Status>((resolve, reject) => {
            const timer = setTimeout(
                () => reject(new Error("no `status` within 15 s")), 15000);
            socket.on("status", (status: Status) => {
                clearTimeout(timer);
                resolve(status);
            });
            socket.on("connect_error", (error) => {
                clearTimeout(timer);
                reject(error);
            });
        });
        expectStatus(told);

        const response = await request.get(serverUrl("/api/v1/status"));
        expect(response.ok()).toBe(true);
        const body = await response.json();
        expect(body.ok).toBe(true);
        expectStatus(body.data);
        // Asked after the event: the same status or a newer one.
        expect(body.data.revision).toBeGreaterThanOrEqual(told.revision);
    } finally {
        socket.disconnect();
    }
});
