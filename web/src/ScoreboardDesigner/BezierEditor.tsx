import React from "react";
import {Box, Slider, Stack, Typography} from "@mui/material";
import {CubicBezier} from "./types";

interface Props {
    value: CubicBezier;
    onChange: (value: CubicBezier) => void;
}

const SIZE = 180;
const PAD = 18;
const INNER = SIZE - PAD * 2;

function clamp01(value: number): number {
    return Math.max(0, Math.min(1, value));
}

function pointToSvg(x: number, y: number): {x: number; y: number} {
    return {
        x: PAD + x * INNER,
        y: PAD + (1 - y) * INNER,
    };
}

function svgToPoint(clientX: number, clientY: number, svg: SVGSVGElement): {x: number; y: number} {
    const rect = svg.getBoundingClientRect();
    return {
        x: clamp01((clientX - rect.left - PAD) / INNER),
        y: clamp01(1 - (clientY - rect.top - PAD) / INNER),
    };
}

export default function BezierEditor({value, onChange}: Props) {
    const svgRef = React.useRef<SVGSVGElement>(null);
    const [dragging, setDragging] = React.useState<"p1" | "p2" | null>(null);
    const p1 = pointToSvg(value.x1, value.y1);
    const p2 = pointToSvg(value.x2, value.y2);
    const start = pointToSvg(0, 0);
    const end = pointToSvg(1, 1);

    React.useEffect(() => {
        if (!dragging) return undefined;
        const move = (event: PointerEvent) => {
            const svg = svgRef.current;
            if (!svg) return;
            const point = svgToPoint(event.clientX, event.clientY, svg);
            onChange(dragging === "p1"
                ? {...value, x1: point.x, y1: point.y}
                : {...value, x2: point.x, y2: point.y});
        };
        const up = () => setDragging(null);
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
        return () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
    }, [dragging, onChange, value]);

    const update = (key: keyof CubicBezier, next: number) =>
        onChange({...value, [key]: clamp01(next)});

    return (
        <Stack gap={1}>
            <Typography variant="caption">イージング</Typography>
            <Box className="designer-bezier-editor">
                <svg
                    ref={svgRef}
                    width={SIZE}
                    height={SIZE}
                    viewBox={`0 0 ${SIZE} ${SIZE}`}
                    aria-label="ベジエグラフ"
                >
                    <rect x={PAD} y={PAD} width={INNER} height={INNER} />
                    <line x1={start.x} y1={start.y} x2={p1.x} y2={p1.y} />
                    <line x1={end.x} y1={end.y} x2={p2.x} y2={p2.y} />
                    <path
                        d={`M ${start.x} ${start.y} C ${p1.x} ${p1.y}, ${p2.x} ${p2.y}, ${end.x} ${end.y}`}
                    />
                    <circle cx={p1.x} cy={p1.y} r={7} onPointerDown={() => setDragging("p1")} />
                    <circle cx={p2.x} cy={p2.y} r={7} onPointerDown={() => setDragging("p2")} />
                </svg>
                <Stack gap={0.5} className="designer-bezier-sliders">
                    {(["x1", "y1", "x2", "y2"] as const).map((key) => (
                        <Stack key={key} direction="row" gap={1} alignItems="center">
                            <Typography variant="caption" sx={{width: 22}}>
                                {key}
                            </Typography>
                            <Slider
                                size="small"
                                min={0}
                                max={1}
                                step={0.01}
                                value={value[key]}
                                aria-label={`bezier ${key}`}
                                onChange={(_, next) => update(key, next as number)}
                            />
                        </Stack>
                    ))}
                </Stack>
            </Box>
        </Stack>
    );
}
