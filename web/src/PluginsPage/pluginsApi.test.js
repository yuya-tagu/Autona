// The plugins page's shell requests (ADR-009 D4, pluginsApi.ts): pickFile /
// pickFolder answer the pick id and name (never a path) or null for a closed
// dialog, openPluginLink names the link; a refusal rejects with the
// desktop's code. How the request reaches the desktop is hostApi.ts's
// (shellRequest.test.js).

jest.mock("../websocketConnection", () => ({
    __esModule: true,
    default: {instance: () => ({connected: false, emit: () => {}})},
}));

const mockShellRequest = jest.fn();
jest.mock("../PanelShell/hostApi", () => ({
    ...jest.requireActual("../PanelShell/hostApi"),
    shellRequest: (...args) => mockShellRequest(...args),
}));

const {httpPluginsApi} = require("./pluginsApi");

beforeEach(() => {
    mockShellRequest.mockReset();
});

test("a reload waits longer than the host's 60 s describe", async () => {
    const post = jest.spyOn(require("../api"), "apiPost")
        .mockResolvedValue("");
    await httpPluginsApi.reload("me.sandbox");
    expect(post).toHaveBeenCalledWith(
        "/api/v1/plugins/me.sandbox:reload", undefined,
        {timeoutMs: 75000});
    expect(75000).toBeGreaterThan(60000);
    post.mockRestore();
});

test("a file is picked among the field's extensions, by id and name", async () => {
    mockShellRequest.mockResolvedValue(
        {ok: true, status: "picked", id: "p1", name: "logo.png"});
    await expect(httpPluginsApi.pickFile(["png", "jpg", "png"]))
        .resolves.toEqual({id: "p1", name: "logo.png"});
    expect(mockShellRequest).toHaveBeenCalledWith(
        "pickFile", {extensions: "png,jpg"});
});

test("a folder is picked for a plugin", async () => {
    mockShellRequest.mockResolvedValue(
        {ok: true, status: "picked", id: "p2", name: "MyFolder"});
    await expect(httpPluginsApi.pickFolder())
        .resolves.toEqual({id: "p2", name: "MyFolder"});
    expect(mockShellRequest).toHaveBeenCalledWith(
        "pickFolder", {purpose: "plugin"});
});

test("a pick without a name is still a pick; a closed dialog is null", async () => {
    mockShellRequest.mockResolvedValueOnce(
        {ok: true, status: "picked", id: "p3"});
    await expect(httpPluginsApi.pickFolder()).resolves.toEqual({id: "p3"});
    mockShellRequest.mockResolvedValueOnce({ok: true, status: "cancelled"});
    await expect(httpPluginsApi.pickFile(["png"])).resolves.toBeNull();
    // A game folder's answer (pick, not id) is not a plugin's pick.
    mockShellRequest.mockResolvedValueOnce(
        {ok: true, status: "picked", pick: "g1", name: "games"});
    await expect(httpPluginsApi.pickFolder()).resolves.toBeNull();
});

test("a refusal rejects with the desktop's code", async () => {
    mockShellRequest.mockResolvedValue({ok: false, error: "bad_extension"});
    await expect(httpPluginsApi.pickFile(["png"]))
        .rejects.toMatchObject({code: "bad_extension"});
    mockShellRequest.mockResolvedValue({ok: false});
    await expect(httpPluginsApi.pickFolder())
        .rejects.toMatchObject({code: "failed"});
    const timeout = Object.assign(new Error("t"), {code: "timeout"});
    mockShellRequest.mockRejectedValue(timeout);
    await expect(httpPluginsApi.pickFolder()).rejects.toBe(timeout);
});

test("extensions the desktop would refuse are not asked for", async () => {
    for (const extensions of [[], ["PNG"], [".png"], ["*"],
                              Array(21).fill(0).map((_, i) => `e${i}`)]) {
        await expect(httpPluginsApi.pickFile(extensions))
            .rejects.toMatchObject({code: "bad_argument"});
    }
    expect(mockShellRequest).not.toHaveBeenCalled();
});

test("a link is named, never a URL", async () => {
    mockShellRequest.mockResolvedValue({ok: true});
    await expect(httpPluginsApi.openLink("installed", "a.b", "funding"))
        .resolves.toBeUndefined();
    expect(mockShellRequest).toHaveBeenCalledWith(
        "openPluginLink", {source: "installed", id: "a.b", link: "funding"});
    mockShellRequest.mockResolvedValue({ok: false, error: "no_link"});
    await expect(httpPluginsApi.openLink("installed", "a.b", "homepage"))
        .rejects.toMatchObject({code: "no_link"});
});
