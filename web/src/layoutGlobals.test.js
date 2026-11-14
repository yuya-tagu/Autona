// The overlays' own loads, in layout/include/globals.js (a plain script every
// layout loads; it cannot be imported, so its functions are read out of it as
// stateDelta.test.js reads applyDelta).
//
// What they keep from happening: a request that fails at the network level
// (the CI runner's E2E, a server still starting) left LoadEverything()'s
// promise rejected with the failed script's error event, or with the Japanese
// dictionary request's ProgressEvent, which no layout catches -- an uncaught
// "Event" / "ProgressEvent", and an overlay that never starts.

const source = require("fs").readFileSync(
    require("path").join(__dirname, "../../layout/include/globals.js"), "utf8");

function declaration(name) {
    const found = new RegExp(
        `(?:async )?function ${name}\\([\\s\\S]*?\\r?\\n\\}\\r?\\n`).exec(source);
    if (!found) {
        throw new Error(`${name} is not in globals.js`);
    }
    return found[0];
}

function constant(name) {
    const found = new RegExp(`var ${name} = (\\d+);`).exec(source);
    if (!found) {
        throw new Error(`${name} is not in globals.js`);
    }
    return Number(found[1]);
}

const ATTEMPTS = constant("TSH_LOAD_ATTEMPTS");

// The functions, with the globals they read. No wait between the tries.
function overlay({Kuroshiro, KuromojiAnalyzer} = {}) {
    // eslint-disable-next-line no-new-func
    return new Function("Kuroshiro", "KuromojiAnalyzer", "tsh_settings", "_", `
        var TSH_LOAD_ATTEMPTS = ${ATTEMPTS};
        var TSH_LOAD_RETRY_MS = 0;
        function TSHLayoutPath(path) { return "../" + path; }
        ${declaration("TSHRetryLoad")}
        ${declaration("TSHLoadScript")}
        ${declaration("LoadKuroshiro")}
        ${declaration("Transcript")}
        return {TSHRetryLoad, TSHLoadScript, LoadKuroshiro, Transcript};
    `)(Kuroshiro, KuromojiAnalyzer, {},
       {defaultsDeep: (value, defaults) => ({...defaults, ...(value || {})})});
}

// Kuroshiro and its analyzer as the libraries behave: an init that fails
// leaves the instance uninitialised. `failures` analyzers fail to read the
// dictionary, with the request's ProgressEvent, before one reads it.
function kuroshiroWith(failures) {
    const analyzers = [];
    class KuromojiAnalyzer {
        constructor(options) {
            this.options = options;
            analyzers.push(this);
        }

        init() {
            return analyzers.length <= failures
                ? Promise.reject(new ProgressEvent("error"))
                : Promise.resolve();
        }
    }
    class Instance {
        async init(analyzer) {
            if (this.analyzer) {
                throw new Error("Kuroshiro has already been initialized.");
            }
            await analyzer.init();
            this.analyzer = analyzer;
        }
    }
    return {Kuroshiro: {default: Instance}, KuromojiAnalyzer, analyzers};
}

beforeEach(() => {
    delete window.kuroshiro;
    delete window.Kuroshiro;
    document.head.innerHTML = "";
});

describe("TSHRetryLoad", () => {
    it("tries a failed load again, up to its last try", async () => {
        const {TSHRetryLoad} = overlay();
        let tries = 0;
        const load = () => (++tries < ATTEMPTS
            ? Promise.reject(new Event("error")) : Promise.resolve("loaded"));

        await expect(TSHRetryLoad(load)).resolves.toBe("loaded");
        expect(tries).toBe(ATTEMPTS);
    });

    it("rejects with the last failure when every try failed", async () => {
        const {TSHRetryLoad} = overlay();
        let tries = 0;
        const load = () => Promise.reject(new Error(`try ${++tries}`));

        await expect(TSHRetryLoad(load)).rejects.toThrow(`try ${ATTEMPTS}`);
        expect(tries).toBe(ATTEMPTS);
    });

    it("is tried more than once", () => {
        expect(ATTEMPTS).toBeGreaterThan(1);
    });
});

describe("TSHLoadScript", () => {
    const src = "../include/gsap.min.js";

    it("resolves once the script has loaded", async () => {
        const {TSHLoadScript} = overlay();
        const loading = TSHLoadScript(src);
        document.head.querySelector("script").dispatchEvent(new Event("load"));

        await expect(loading).resolves.toBeDefined();
        expect(document.head.querySelectorAll("script")).toHaveLength(1);
    });

    it("rejects with an Error naming the script, not the bare event", async () => {
        const {TSHLoadScript} = overlay();
        const loading = TSHLoadScript(src);
        const element = document.head.querySelector("script");
        expect(element.getAttribute("src")).toBe(src);
        const event = new Event("error");
        element.dispatchEvent(event);

        const error = await loading.catch((reason) => reason);
        expect(error).toBeInstanceOf(Error);
        expect(error.message).toContain(src);
        expect(error.cause).toBe(event);
        // Taken out: the next try adds a fresh element.
        expect(document.head.querySelector("script")).toBeNull();
    });
});

describe("LoadKuroshiro", () => {
    it("reads the dictionary on a later try, with a fresh analyzer", async () => {
        const {Kuroshiro, KuromojiAnalyzer, analyzers} = kuroshiroWith(ATTEMPTS - 1);
        const {LoadKuroshiro} = overlay({Kuroshiro, KuromojiAnalyzer});

        await expect(LoadKuroshiro()).resolves.toBeUndefined();
        expect(analyzers).toHaveLength(ATTEMPTS);
        expect(window.kuroshiro).toBeInstanceOf(Kuroshiro.default);
        expect(window.kuroshiro.analyzer).toBe(analyzers[ATTEMPTS - 1]);
        expect(analyzers[0].options).toEqual({dictPath: "../include/kuromoji"});
    });

    it("starts the overlay without the reading when the dictionary is not read", async () => {
        const {Kuroshiro, KuromojiAnalyzer, analyzers} = kuroshiroWith(Infinity);
        const {LoadKuroshiro, Transcript} = overlay({Kuroshiro, KuromojiAnalyzer});
        const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
        try {
            // Resolves: InitAll goes on to connect and show the state.
            await expect(LoadKuroshiro()).resolves.toBeUndefined();
            expect(analyzers).toHaveLength(ATTEMPTS);
            expect(warn).toHaveBeenCalledTimes(1);
            expect(warn.mock.calls[0][1]).toBeInstanceOf(ProgressEvent);
        } finally {
            warn.mockRestore();
        }
        expect(window.kuroshiro).toBeNull();
        // The name as it is.
        await expect(Promise.resolve(Transcript("とうきょう"))).resolves.toBe("とうきょう");
    });
});

describe("Transcript", () => {
    it("adds the reading when the dictionary was read", async () => {
        const {Transcript} = overlay();
        window.Kuroshiro = {default: {Util: {hasJapanese: () => true}}};
        window.kuroshiro = {convert: () => Promise.resolve("toukyou")};

        await expect(Promise.resolve(Transcript("とうきょう"))).resolves.toBe(
            'とうきょう<span class="tsh_transcript">&nbsp;toukyou</span>');
    });
});
