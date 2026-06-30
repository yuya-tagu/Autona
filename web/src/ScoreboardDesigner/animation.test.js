import {animationKeyframes} from "./animation";

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
});
