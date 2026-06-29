import React from "react";
import {
    Box,
    FormControlLabel,
    MenuItem,
    Select,
    Slider,
    Stack,
    Switch,
    TextField,
    Typography,
} from "@mui/material";
import {AnimationPreset, DesignerLayer} from "./types";

interface Props {
    layer: DesignerLayer | null;
    onChange: (patch: Partial<DesignerLayer>) => void;
}

const PRESETS: Array<{value: AnimationPreset; label: string}> = [
    {value: "none", label: "なし"},
    {value: "fade", label: "フェード"},
    {value: "slide-left", label: "左からスライド"},
    {value: "slide-right", label: "右からスライド"},
    {value: "slide-up", label: "上からスライド"},
    {value: "slide-down", label: "下からスライド"},
    {value: "zoom", label: "ズーム"},
];

function NumberField({
    label,
    value,
    onChange,
    min,
    max,
}: {
    label: string;
    value: number;
    onChange: (value: number) => void;
    min?: number;
    max?: number;
}) {
    return (
        <TextField
            size="small"
            type="number"
            label={label}
            value={Number.isFinite(value) ? value : 0}
            inputProps={{min, max}}
            onChange={(event) => {
                const next = Number(event.target.value);
                if (Number.isFinite(next)) onChange(next);
            }}
        />
    );
}

function ColorField({
    label,
    value,
    onChange,
}: {
    label: string;
    value: string;
    onChange: (value: string) => void;
}) {
    return (
        <Stack direction="row" gap={1} alignItems="center">
            <input
                type="color"
                aria-label={label}
                value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#ffffff"}
                onChange={(event) => onChange(event.target.value)}
            />
            <TextField
                size="small"
                label={label}
                value={value}
                onChange={(event) => onChange(event.target.value)}
                fullWidth
            />
        </Stack>
    );
}

export default function LayerInspector({layer, onChange}: Props) {
    if (!layer) {
        return (
            <Box className="designer-empty-inspector">
                <Typography color="text.secondary">
                    キャンバスまたは一覧からレイヤーを選択してください。
                </Typography>
            </Box>
        );
    }

    const updateAnimation = (patch: Partial<DesignerLayer["animation"]>) =>
        onChange({animation: {...layer.animation, ...patch}} as Partial<DesignerLayer>);

    return (
        <Stack gap={1.5} className="designer-inspector-fields">
            <Typography variant="overline">基本</Typography>
            <TextField
                size="small"
                label="レイヤー名"
                value={layer.name}
                onChange={(event) => onChange({name: event.target.value})}
            />
            <FormControlLabel
                control={
                    <Switch
                        size="small"
                        checked={layer.visible}
                        onChange={(event) => onChange({visible: event.target.checked})}
                    />
                }
                label="表示"
            />
            <div className="designer-field-grid">
                <NumberField label="X" value={layer.x} onChange={(x) => onChange({x})} />
                <NumberField label="Y" value={layer.y} onChange={(y) => onChange({y})} />
                <NumberField
                    label="幅"
                    value={layer.width}
                    min={10}
                    onChange={(width) => onChange({width: Math.max(10, width)})}
                />
                <NumberField
                    label="高さ"
                    value={layer.height}
                    min={10}
                    onChange={(height) => onChange({height: Math.max(10, height)})}
                />
                <NumberField
                    label="回転"
                    value={layer.rotation}
                    onChange={(rotation) => onChange({rotation})}
                />
                <NumberField
                    label="重なり順"
                    value={layer.zIndex}
                    onChange={(zIndex) => onChange({zIndex})}
                />
            </div>
            <Typography variant="caption">透明度 {Math.round(layer.opacity * 100)}%</Typography>
            <Slider
                size="small"
                value={layer.opacity}
                min={0}
                max={1}
                step={0.01}
                onChange={(_, opacity) => onChange({opacity: opacity as number})}
            />

            {layer.type === "image" && (
                <>
                    <Typography variant="overline">PNG</Typography>
                    <Typography variant="caption" color="text.secondary">
                        {layer.asset}
                    </Typography>
                    <Select
                        size="small"
                        value={layer.objectFit}
                        onChange={(event) => onChange({
                            objectFit: event.target.value,
                        } as Partial<DesignerLayer>)}
                    >
                        <MenuItem value="contain">比率を維持</MenuItem>
                        <MenuItem value="cover">領域を覆う</MenuItem>
                        <MenuItem value="fill">領域に合わせて変形</MenuItem>
                    </Select>
                </>
            )}

            {layer.type === "data-image" && (
                <>
                    <Typography variant="overline">動的画像</Typography>
                    <Typography variant="body2">{layer.binding}</Typography>
                    <Select
                        size="small"
                        value={layer.objectFit}
                        onChange={(event) => onChange({
                            objectFit: event.target.value,
                        } as Partial<DesignerLayer>)}
                    >
                        <MenuItem value="contain">比率を維持</MenuItem>
                        <MenuItem value="cover">領域を覆う</MenuItem>
                        <MenuItem value="fill">領域に合わせて変形</MenuItem>
                    </Select>
                </>
            )}

            {layer.type === "data-text" && (
                <>
                    <Typography variant="overline">動的テキスト</Typography>
                    <Typography variant="body2">{layer.binding}</Typography>
                    <TextField
                        size="small"
                        label="フォント"
                        value={layer.typography.fontFamily}
                        onChange={(event) => onChange({
                            typography: {
                                ...layer.typography,
                                fontFamily: event.target.value,
                            },
                        } as Partial<DesignerLayer>)}
                    />
                    <Stack gap={0.5}>
                        <Typography variant="caption">
                            文字サイズ {layer.typography.fontSize}px
                        </Typography>
                        <Slider
                            size="small"
                            value={layer.typography.fontSize}
                            min={8}
                            max={160}
                            step={1}
                            aria-label="文字サイズ"
                            onChange={(_, fontSize) => onChange({
                                typography: {
                                    ...layer.typography,
                                    fontSize: fontSize as number,
                                },
                            } as Partial<DesignerLayer>)}
                        />
                    </Stack>
                    <div className="designer-field-grid">
                        <NumberField
                            label="太さ"
                            value={layer.typography.fontWeight}
                            min={100}
                            max={900}
                            onChange={(fontWeight) => onChange({
                                typography: {...layer.typography, fontWeight},
                            } as Partial<DesignerLayer>)}
                        />
                        <NumberField
                            label="文字間隔"
                            value={layer.typography.letterSpacing}
                            onChange={(letterSpacing) => onChange({
                                typography: {...layer.typography, letterSpacing},
                            } as Partial<DesignerLayer>)}
                        />
                    </div>
                    <ColorField
                        label="文字色"
                        value={layer.typography.color}
                        onChange={(color) => onChange({
                            typography: {...layer.typography, color},
                        } as Partial<DesignerLayer>)}
                    />
                    <Select
                        size="small"
                        value={layer.typography.align}
                        onChange={(event) => onChange({
                            typography: {
                                ...layer.typography,
                                align: event.target.value,
                            },
                        } as Partial<DesignerLayer>)}
                    >
                        <MenuItem value="left">左揃え</MenuItem>
                        <MenuItem value="center">中央揃え</MenuItem>
                        <MenuItem value="right">右揃え</MenuItem>
                    </Select>
                    <FormControlLabel
                        control={
                            <Switch
                                size="small"
                                checked={layer.typography.italic}
                                onChange={(event) => onChange({
                                    typography: {
                                        ...layer.typography,
                                        italic: event.target.checked,
                                    },
                                } as Partial<DesignerLayer>)}
                            />
                        }
                        label="斜体"
                    />
                </>
            )}

            {layer.type === "game-history" && (
                <>
                    <Typography variant="overline">ゲーム取得表示</Typography>
                    <NumberField
                        label="間隔"
                        value={layer.gap}
                        min={0}
                        onChange={(gap) => onChange({gap} as Partial<DesignerLayer>)}
                    />
                    <ColorField
                        label="P1色"
                        value={layer.p1Color}
                        onChange={(p1Color) => onChange({p1Color} as Partial<DesignerLayer>)}
                    />
                    <ColorField
                        label="P2色"
                        value={layer.p2Color}
                        onChange={(p2Color) => onChange({p2Color} as Partial<DesignerLayer>)}
                    />
                    <ColorField
                        label="未取得色"
                        value={layer.neutralColor}
                        onChange={(neutralColor) => onChange({neutralColor} as Partial<DesignerLayer>)}
                    />
                </>
            )}

            <Typography variant="overline">開始アニメーション</Typography>
            <Select
                size="small"
                value={layer.animation.preset}
                onChange={(event) => updateAnimation({
                    preset: event.target.value as AnimationPreset,
                })}
            >
                {PRESETS.map((preset) => (
                    <MenuItem key={preset.value} value={preset.value}>
                        {preset.label}
                    </MenuItem>
                ))}
            </Select>
            <div className="designer-field-grid">
                <NumberField
                    label="時間(ms)"
                    value={layer.animation.durationMs}
                    min={0}
                    onChange={(durationMs) => updateAnimation({durationMs})}
                />
                <NumberField
                    label="遅延(ms)"
                    value={layer.animation.delayMs}
                    min={0}
                    onChange={(delayMs) => updateAnimation({delayMs})}
                />
            </div>
        </Stack>
    );
}
