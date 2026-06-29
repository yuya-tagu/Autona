import {
    deleteDesktopProject,
    isDesktopDesigner,
    listDesktopProjects,
    saveDesktopProject,
} from "./desktopApi";
import {createEmptyProject} from "./model";

describe("desktop Designer API", () => {
    beforeEach(() => {
        window.history.replaceState(
            {}, "", "/web?view=designer&desktop=1&token=desktop-token");
        global.fetch = jest.fn(async (url, init) => {
            if (url === "/api/designer/projects" && init?.method === "POST") {
                return {
                    ok: true,
                    json: async () => ({
                        slug: "finals",
                        path: "layout/custom/finals",
                    }),
                };
            }
            return {
                ok: true,
                json: async () => ({
                    projects: [{slug: "finals", name: "Finals"}],
                }),
            };
        });
    });

    test("requires the desktop URL marker and token", () => {
        expect(isDesktopDesigner()).toBe(true);
        window.history.replaceState({}, "", "/web?view=designer");
        expect(isDesktopDesigner()).toBe(false);
    });

    test("lists projects with the desktop token", async () => {
        await expect(listDesktopProjects()).resolves.toEqual([
            {slug: "finals", name: "Finals"},
        ]);
        expect(global.fetch).toHaveBeenCalledWith(
            "/api/designer/projects",
            expect.objectContaining({
                headers: expect.objectContaining({
                    "X-Autona-Designer-Token": "desktop-token",
                }),
            }),
        );
    });

    test("saves a project without a filesystem picker", async () => {
        const result = await saveDesktopProject(createEmptyProject("Finals"), {});
        expect(result.path).toBe("layout/custom/finals");
        const [, init] = global.fetch.mock.calls[0];
        expect(JSON.parse(init.body)).toMatchObject({
            project: {name: "Finals"},
            assets: {},
        });
    });

    test("deletes a project with the desktop token", async () => {
        global.fetch.mockResolvedValueOnce({
            ok: true,
            json: async () => ({slug: "finals"}),
        });

        await expect(deleteDesktopProject("finals")).resolves.toEqual({
            slug: "finals",
        });
        expect(global.fetch).toHaveBeenCalledWith(
            "/api/designer/projects/finals",
            expect.objectContaining({
                method: "DELETE",
                headers: expect.objectContaining({
                    "X-Autona-Designer-Token": "desktop-token",
                }),
            }),
        );
    });
});
