import {
    animationKeyframes,
    bezierToCss,
    ChangeTransitionCoordinator,
    motionAnimationKeyframes,
    visualTimelineKeyframes,
} from "./animation";

describe("scoreboard layer animation", () => {
    test("creates slide and zoom keyframes", () => {
        expect(animationKeyframes("slide-left")[0]).toMatchObject({
            opacity: 0,
            transform: "translate(-80px, 0)",
        });
        expect(animationKeyframes("zoom")[0]).toMatchObject({
            transform: "scale(0.72)",
        });
    });

    test("preserves configured final opacity", () => {
        const frames = animationKeyframes("fade", 0.4);
        expect(frames[frames.length - 1]).toMatchObject({opacity: 0.4});
    });

    test("converts motion keyframes to web animation transforms", () => {
        const frames = motionAnimationKeyframes({
            renderer: "webAnimations",
            durationMs: 1000,
            delayMs: 0,
            easing: {x1: 0.2, y1: 0.8, x2: 0.2, y2: 1},
            loop: false,
            keyframes: [{
                id: "a",
                timeMs: 500,
                x: 12,
                y: -8,
                rotation: 15,
                scaleX: 1.2,
                scaleY: 0.9,
            }],
        });

        expect(frames[0]).toMatchObject({
            offset: 0.5,
            transform: "translate(12px, -8px) rotate(15deg) scale(1.2, 0.9)",
        });
        expect(bezierToCss({x1: 0.1, y1: 0.2, x2: 0.3, y2: 0.4}))
            .toBe("cubic-bezier(0.1,0.2,0.3,0.4)");
    });

    test("converts visual timelines including opacity", () => {
        const frames = visualTimelineKeyframes({
            durationMs: 1000,
            delayMs: 0,
            easing: {x1: 0.2, y1: 0.8, x2: 0.2, y2: 1},
            visualKeyframes: [{
                id: "visual",
                timeMs: 250,
                x: 0,
                y: 0,
                rotation: 0,
                scaleX: 1,
                scaleY: 1,
                opacity: 0.25,
            }],
            rendererActions: [],
        });

        expect(frames[0]).toMatchObject({offset: 0.25, opacity: 0.25});
    });

    test("coalesces updates before swap and restarts after swap", () => {
        const commits = [];
        const runs = [];
        const coordinator = new ChangeTransitionCoordinator(
            (left, right) => left === right,
            (value) => commits.push(value),
            (swap, finish) => {
                const run = {swap, finish, cancelled: false};
                runs.push(run);
                return () => {
                    run.cancelled = true;
                };
            },
        );

        coordinator.initialize("A");
        coordinator.update("B");
        coordinator.update("C");
        expect(runs).toHaveLength(1);
        runs[0].swap();
        expect(commits).toEqual(["A", "C"]);

        coordinator.update("D");
        expect(runs[0].cancelled).toBe(true);
        expect(runs).toHaveLength(2);
        runs[1].swap();
        runs[1].finish();
        expect(commits).toEqual(["A", "C", "D"]);
        expect(coordinator.current()).toBe("D");
    });
});
