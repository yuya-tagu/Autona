import {
    AnimationPreset,
    CubicBezier,
    EventTimeline,
    LayerAnimation,
    LayerMotionAnimation,
} from "./types";

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

export function bezierToCss(easing: CubicBezier): string {
    return `cubic-bezier(${easing.x1},${easing.y1},${easing.x2},${easing.y2})`;
}

export function motionAnimationKeyframes(animation: LayerMotionAnimation): Keyframe[] {
    const duration = Math.max(1, animation.durationMs);
    return [...animation.keyframes]
        .sort((a, b) => a.timeMs - b.timeMs)
        .map((frame) => ({
            offset: Math.max(0, Math.min(1, frame.timeMs / duration)),
            transform: [
                `translate(${frame.x}px, ${frame.y}px)`,
                `rotate(${frame.rotation}deg)`,
                `scale(${frame.scaleX}, ${frame.scaleY})`,
            ].join(" "),
        }));
}

export function playMotionAnimation(
    element: HTMLElement,
    animation: LayerMotionAnimation,
): Animation | null {
    if (!animation.keyframes.length) return null;
    return element.animate(motionAnimationKeyframes(animation), {
        duration: Math.max(0, animation.durationMs),
        delay: Math.max(0, animation.delayMs),
        easing: bezierToCss(animation.easing),
        iterations: animation.loop ? Infinity : 1,
        fill: "both",
    });
}

export function visualTimelineKeyframes(timeline: EventTimeline): Keyframe[] {
    const duration = Math.max(1, timeline.durationMs);
    return [...timeline.visualKeyframes]
        .sort((a, b) => a.timeMs - b.timeMs)
        .map((frame) => ({
            offset: Math.max(0, Math.min(1, frame.timeMs / duration)),
            opacity: frame.opacity,
            transform: [
                `translate(${frame.x}px, ${frame.y}px)`,
                `rotate(${frame.rotation}deg)`,
                `scale(${frame.scaleX}, ${frame.scaleY})`,
            ].join(" "),
        }));
}

export function playEventTimeline(
    element: HTMLElement,
    timeline: EventTimeline,
): Animation {
    (element.getAnimations?.() ?? []).forEach((animation) => animation.cancel());
    return element.animate(visualTimelineKeyframes(timeline), {
        duration: Math.max(0, timeline.durationMs),
        delay: Math.max(0, timeline.delayMs),
        easing: bezierToCss(timeline.easing),
        iterations: 1,
        fill: "both",
    });
}

export type TransitionRunner = (
    onSwap: () => void,
    onFinish: () => void,
) => () => void;

/**
 * Keeps rendered data separate from incoming state while a data-change
 * timeline is running. Updates received before the swap point are coalesced;
 * updates received after it cancel the tail and start from the visible value.
 */
export class ChangeTransitionCoordinator<T> {
    private displayed: T | undefined;
    private pending: T | undefined;
    private active: {swapped: boolean; cancel: () => void} | null = null;

    public constructor(
        private readonly equals: (left: T, right: T) => boolean,
        private readonly commit: (value: T) => void,
        private readonly run: TransitionRunner,
    ) {}

    public initialize(value: T): void {
        this.cancel();
        this.displayed = value;
        this.pending = value;
        this.commit(value);
    }

    public update(value: T): void {
        if (this.displayed === undefined) {
            this.initialize(value);
            return;
        }
        if (this.equals(this.pending as T, value)) return;
        this.pending = value;
        if (this.active && !this.active.swapped) return;
        if (this.active) this.active.cancel();
        this.start();
    }

    public cancel(): void {
        this.active?.cancel();
        this.active = null;
    }

    public current(): T | undefined {
        return this.displayed;
    }

    private start(): void {
        if (this.pending === undefined || this.displayed === undefined ||
            this.equals(this.pending, this.displayed)) {
            this.active = null;
            return;
        }
        const state: {swapped: boolean; cancel: () => void} = {
            swapped: false,
            cancel: () => {},
        };
        this.active = state;
        state.cancel = this.run(
            () => {
                if (this.active !== state || this.pending === undefined) return;
                state.swapped = true;
                this.displayed = this.pending;
                this.commit(this.displayed);
            },
            () => {
                if (this.active !== state) return;
                this.active = null;
                if (this.pending !== undefined && this.displayed !== undefined &&
                    !this.equals(this.pending, this.displayed)) {
                    this.start();
                }
            },
        );
    }
}
