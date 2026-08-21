import React from "react";
import {
    InputAdornment,
    TextField,
} from "@mui/material";
import DragIndicator from "@mui/icons-material/DragIndicator";

interface Props {
    label: string;
    value: number;
    onChange: (value: number) => void;
    min?: number;
    max?: number;
    step?: number;
}

function clamp(value: number, min?: number, max?: number): number {
    return Math.min(max ?? Number.POSITIVE_INFINITY,
        Math.max(min ?? Number.NEGATIVE_INFINITY, value));
}

function precisionFor(step: number): number {
    const decimal = String(step).split(".")[1];
    return decimal?.length ?? 0;
}

export default function NumberField({
    label,
    value,
    onChange,
    min,
    max,
    step = 1,
}: Props) {
    const inputRef = React.useRef<HTMLInputElement>(null);
    const editingRef = React.useRef(false);
    const [draft, setDraft] = React.useState(String(
        Number.isFinite(value) ? value : 0,
    ));

    React.useEffect(() => {
        if (!editingRef.current) {
            setDraft(String(Number.isFinite(value) ? value : 0));
        }
    }, [value]);

    const finishEditing = React.useCallback(() => {
        editingRef.current = false;
        setDraft(String(Number.isFinite(value) ? value : 0));
    }, [value]);

    const startScrub = React.useCallback((event: React.PointerEvent) => {
        event.preventDefault();
        event.stopPropagation();
        inputRef.current?.focus();
        editingRef.current = true;
        const parsed = Number(draft);
        const startValue = draft.trim() && Number.isFinite(parsed) ? parsed : value;
        const startX = event.clientX;
        const decimals = precisionFor(step);

        const move = (moveEvent: PointerEvent) => {
            const raw = startValue + (moveEvent.clientX - startX) * step;
            const next = clamp(Number(raw.toFixed(decimals)), min, max);
            setDraft(String(next));
            onChange(next);
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }, [draft, max, min, onChange, step, value]);

    return (
        <TextField
            size="small"
            label={label}
            value={draft}
            inputRef={inputRef}
            inputProps={{min, max, step, inputMode: "decimal"}}
            onFocus={() => {
                editingRef.current = true;
            }}
            onBlur={finishEditing}
            onChange={(event) => {
                const nextDraft = event.target.value;
                setDraft(nextDraft);
                const normalized = nextDraft.trim();
                if (!normalized || ["-", ".", "-."].includes(normalized)) return;
                const next = Number(normalized);
                if (Number.isFinite(next)) onChange(next);
            }}
            onKeyDown={(event) => {
                if (event.key === "Enter") inputRef.current?.blur();
                if (event.key === "Escape") {
                    setDraft(String(Number.isFinite(value) ? value : 0));
                    inputRef.current?.blur();
                }
            }}
            InputProps={{
                startAdornment: (
                    <InputAdornment
                        position="start"
                        className="designer-number-scrubber"
                        aria-label={`${label}をドラッグして調整`}
                        onPointerDown={startScrub}
                    >
                        <DragIndicator fontSize="small" />
                    </InputAdornment>
                ),
            }}
        />
    );
}
