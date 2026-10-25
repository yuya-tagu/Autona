import {ChildProcessWithoutNullStreams, spawn} from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

// Starts the headless app for the whole run and returns the teardown that
// stops it (ADR-005 S3d). The server is e2e/headless_server.py: it builds
// the throwaway working directory the headless smoke test uses, launches
// `python main.py --headless` there and reports the URL on stdout.
//
// Stopping is by this run's own processes only: "stop" on the helper's
// stdin (it then sends Ctrl+Break to the headless process it started), and
// only if the helper does not exit, a kill of the two PIDs it reported.
// Anything but a clean stop fails the run: the helper's
// `AUTONA_E2E_STOPPED {"exit": 0, "killed": false}` and its own exit 0.

const WEB_ROOT = path.resolve(__dirname, "..");
const PROJECT_ROOT = path.resolve(WEB_ROOT, "..");
const START_TIMEOUT_MS = 150_000;
const STOP_TIMEOUT_MS = 90_000;

type Ready = {url: string, pid: number, dir: string};
type Stopped = {exit: number | null, killed: boolean};

// The helper process and what it said on stdout.
class Helper {
    readonly lines: string[] = [];
    stopped: Stopped | undefined;
    private closed = false;
    private readonly whenClosed: Promise<void>;
    private readonly lineListeners: ((line: string) => void)[] = [];

    constructor(readonly child: ChildProcessWithoutNullStreams) {
        // Once the helper is gone its stdin is too: writing "stop" to a
        // closed pipe must not throw (EPIPE) out of the teardown.
        child.stdin.on("error", () => {});
        child.stderr.on("data", (chunk) => process.stderr.write(chunk));
        readline.createInterface({input: child.stdout}).on("line", (line) => {
            this.lines.push(line);
            if (line.startsWith("AUTONA_E2E_STOPPED ")) {
                this.stopped = JSON.parse(line.slice("AUTONA_E2E_STOPPED ".length));
            }
            this.lineListeners.forEach((listener) => listener(line));
        });
        // "close", not "exit": by then every line of its stdout has been read.
        this.whenClosed = new Promise((resolve) => {
            child.once("close", () => {
                this.closed = true;
                resolve();
            });
        });
    }

    // Whether the helper ended (and its stdout was read) within `ms`.
    ended(ms: number): Promise<boolean> {
        if (this.closed) {
            return Promise.resolve(true);
        }
        return new Promise((resolve) => {
            const timer = setTimeout(() => resolve(false), ms);
            this.whenClosed.then(() => {
                clearTimeout(timer);
                resolve(true);
            });
        });
    }

    onLine(listener: (line: string) => void) {
        this.lineListeners.push(listener);
    }

    output(): string {
        return this.lines.join("\n");
    }
}

function python(): string {
    return process.env.AUTONA_E2E_PYTHON
        || (process.platform === "win32" ? "python" : "python3");
}

function killPid(pid: number | undefined) {
    if (!pid) {
        return;
    }
    try {
        process.kill(pid);
    } catch {
        // Already gone.
    }
}

function waitForReady(helper: Helper): Promise<Ready> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            reject(new Error(
                `the headless server did not start in ${START_TIMEOUT_MS / 1000} s\n`
                + helper.output()));
        }, START_TIMEOUT_MS);
        helper.onLine((line) => {
            if (line.startsWith("AUTONA_E2E_READY ")) {
                clearTimeout(timer);
                resolve(JSON.parse(line.slice("AUTONA_E2E_READY ".length)));
            } else if (line.startsWith("AUTONA_E2E_FAILED ")) {
                clearTimeout(timer);
                reject(new Error(
                    "the headless server did not start: "
                    + line.slice("AUTONA_E2E_FAILED ".length)));
            }
        });
        helper.child.once("exit", (code) => {
            clearTimeout(timer);
            reject(new Error(
                `the headless server helper exited (${code}) before serving\n`
                + helper.output()));
        });
    });
}

export default async function globalSetup() {
    if (!fs.existsSync(path.join(WEB_ROOT, "build", "index.html"))) {
        throw new Error(
            "web/build/index.html is missing: run `npm run build` in web/ "
            + "before `npm run test:e2e` (the headless app serves the built UI).");
    }

    const helper = new Helper(spawn(
        python(),
        [
            "-B",
            path.join(__dirname, "headless_server.py"),
            "--console-copy",
            path.join(WEB_ROOT, "test-results", "headless-console.txt"),
        ],
        {cwd: PROJECT_ROOT, stdio: ["pipe", "pipe", "pipe"], windowsHide: true},
    ));
    const child = helper.child;

    let ready: Ready;
    try {
        ready = await waitForReady(helper);
    } catch (error) {
        child.stdin.end();
        if (!await helper.ended(STOP_TIMEOUT_MS)) {
            killPid(child.pid);
        }
        throw error;
    }
    process.env.AUTONA_E2E_BASE_URL = ready.url;

    return async () => {
        child.stdin.end("stop\n");
        if (!await helper.ended(STOP_TIMEOUT_MS)) {
            killPid(child.pid);
            killPid(ready.pid);
            await helper.ended(10_000);
            throw new Error(
                `the headless server helper did not stop; killed PIDs ${child.pid} and ${ready.pid}`);
        }
        const stopped = helper.stopped;
        if (child.exitCode !== 0 || !stopped || stopped.exit !== 0 || stopped.killed) {
            throw new Error(
                "the headless server did not stop cleanly (helper exit "
                + `${child.exitCode}, headless ${JSON.stringify(stopped ?? null)}); `
                + "see test-results/headless-console.txt\n" + helper.output());
        }
    };
}
