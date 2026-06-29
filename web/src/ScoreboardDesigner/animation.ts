import {AnimationPreset, LayerAnimation} from "./types";

export function animationKeyframes(
    preset: AnimationPreset,
    opacity = 1,
): Keyframe[] {
    const end = {opacity, transform: "translate(0, 0) scale(1)"};
    switch (preset) {
        case "fade":
            return [{opacity: 0}, end];
        case "slide-left":
            return [{opacity: 0, transform: "translate(-80px, 0)"}, end];
        case "slide-right":
            return [{opacity: 0, transform: "translate(80px, 0)"}, end];
        case "slide-up":
            return [{opacity: 0, transform: "translate(0, -80px)"}, end];
        case "slide-down":
            return [{opacity: 0, transform: "translate(0, 80px)"}, end];
        case "zoom":
            return [{opacity: 0, transform: "scale(0.72)"}, end];
        default:
            return [end, end];
    }
}

export function playLayerAnimation(
    element: HTMLElement,
    animation: LayerAnimation,
    opacity = 1,
): Animation | null {
    if (animation.preset === "none") return null;
    return element.animate(animationKeyframes(animation.preset, opacity), {
        duration: Math.max(0, animation.durationMs),
        delay: Math.max(0, animation.delayMs),
        easing: "cubic-bezier(.2,.8,.2,1)",
        fill: "both",
    });
}
