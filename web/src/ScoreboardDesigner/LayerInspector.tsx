import React from "react";
import {
    Box,
    Button,
    FormControlLabel,
    MenuItem,
    Select,
    Slider,
    Stack,
    Switch,
    TextField,
    Typography,
} from "@mui/material";
import {
    AnimationPreset,
    BINDINGS,
    DesignerLayer,
    GAME_HISTORY_PART_TARGETS,
    GameHistoryLayerAssignment,
    GameHistoryPartTarget,
    GameHistoryImagePartRole,
} from "./types";
import {
    BINDING_LABELS,
    createDefaultAnimations,
    createDefaultEvents,
    createDefaultGameHistoryImageStyle,
} from "./model";
import NumberField from "./NumberField";

interface Props {
    layer: DesignerLayer | null;
    gameHistoryAssignments?: GameHistoryLayerAssignment[];
    onGameHistoryAssignmentSelect?: (layerId: string) => void;
    allowGameHistory?: boolean;
    gameHistoryPartEditing?: boolean;
    selectedGameHistoryPartPath?: number[] | null;
    onGameHistoryPartEditingChange?: (enabled: boolean) => void;
    onGameHistoryPartTargetAssign?: (target: GameHistoryPartTarget) => void;
    onGameHistoryPartTargetRemove?: (target: GameHistoryPartTarget) => void;
    onGameHistoryPartRoleChange?: (
        role: GameHistoryImagePartRole,
        setIndex?: number,
    ) => void;
    onGameHistoryPartRemove?: () => void;
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
const GAME_HISTORY_SET_COUNT = 3;
const TEXT_BINDINGS = BINDINGS.filter((binding) => ![
    "p1.sponsorLogo",
    "p1.countryFlag",
    "p2.sponsorLogo",
    "p2.countryFlag",
    "tournament.logo",
    "games",
].includes(binding));

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

function gameHistoryTargetLabel(target: GameHistoryPartTarget): string {
    const playerLabel = target.role === "p1-set" ? "P1" : "P2";
    return target.setIndex === 2
        ? `共通セット3（${playerLabel}）`
        : `${playerLabel}セット${target.setIndex + 1}`;
}

function GameHistoryAssignmentSummary({
    assignments,
    onSelect,
}: {
    assignments: GameHistoryLayerAssignment[];
    onSelect?: (layerId: string) => void;
}) {
    return (
        <Stack gap={0.75}>
            <Typography variant="overline">Game-History SVG設定状況</Typography>
            <Typography variant="caption" color="text.secondary">
                P1/P2のセット1・2と、P1/P2で共有するセット3を表示しています。
            </Typography>
            <Box sx={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0.5}}>
                {Array.from({length: GAME_HISTORY_SET_COUNT}, (_, setIndex) =>
                    GAME_HISTORY_PART_TARGETS
                        .filter((target) => target.setIndex === setIndex)
                        .map((target) => {
                            const assignment = assignments.find((item) =>
                                item.target.role === target.role &&
                                item.target.setIndex === target.setIndex);
                            const content = (
                                <Stack alignItems="flex-start" minWidth={0}>
                                    <Typography variant="caption">
                                        {gameHistoryTargetLabel(target)}
                                    </Typography>
                                    <Typography
                                        variant="caption"
                                        color={assignment?.layerName ? "text.primary" : "text.secondary"}
                                        noWrap
                                        width="100%"
                                    >
                                        {assignment?.layerName ?? "未指定"}
                                    </Typography>
                                </Stack>
                            );
                            if (assignment?.layerId && onSelect) {
                                return (
                                    <Button
                                        key={`${target.role}-${target.setIndex}`}
                                        size="small"
                                        variant="outlined"
                                        onClick={() => onSelect(assignment.layerId!)}
                                        aria-label={`${gameHistoryTargetLabel(target)}のSVGレイヤーを選択`}
                                        sx={{justifyContent: "flex-start", textAlign: "left", minWidth: 0}}
                                    >
                                        {content}
                                    </Button>
                                );
                            }
                            return (
                                <Box
                                    key={`${target.role}-${target.setIndex}`}
                                    sx={{border: 1, borderColor: "divider", borderRadius: 1, px: 1, py: 0.5}}
                                >
                                    {content}
                                </Box>
                            );
                        }),
                )}
            </Box>
        </Stack>
    );
}

export default function LayerInspector({
    layer,
    gameHistoryAssignments,
    onGameHistoryAssignmentSelect,
    allowGameHistory = false,
    gameHistoryPartEditing = false,
    selectedGameHistoryPartPath = null,
    onGameHistoryPartEditingChange,
    onGameHistoryPartTargetAssign,
    onGameHistoryPartTargetRemove,
    onGameHistoryPartRoleChange,
    onGameHistoryPartRemove,
    onChange,
}: Props) {
    const assignmentSummary = gameHistoryAssignments ? (
        <GameHistoryAssignmentSummary
            assignments={gameHistoryAssignments}
            onSelect={onGameHistoryAssignmentSelect}
        />
    ) : null;
    if (!layer) {
        return (
            <Stack gap={1.5} className="designer-inspector-fields">
                {assignmentSummary}
                <Box className="designer-empty-inspector">
                    <Typography color="text.secondary">
                        キャンバスまたは一覧からレイヤーを選択してください。
                    </Typography>
                </Box>
            </Stack>
        );
    }

    const selectedGameHistoryPart = layer.type === "image"
        ? layer.gameHistoryStyle?.parts?.find((part) =>
            selectedGameHistoryPartPath !== null &&
            part.path.length === selectedGameHistoryPartPath.length &&
            part.path.every((value, index) => value === selectedGameHistoryPartPath[index]))
        : undefined;
    const selectedPartValue = selectedGameHistoryPart?.role === "winner"
        ? "winner"
        : selectedGameHistoryPart?.role === "set"
            ? `set:${selectedGameHistoryPart.setIndex ?? 0}`
            : selectedGameHistoryPart?.role === "p1-set"
                ? `p1-set:${selectedGameHistoryPart.setIndex ?? 0}`
                : selectedGameHistoryPart?.role === "p2-set"
                    ? `p2-set:${selectedGameHistoryPart.setIndex ?? 0}`
                    : "";

    const updateAnimation = (
        patch: Partial<NonNullable<DesignerLayer["animation"]>>,
    ) => {
        const animation = {
            preset: "fade",
            durationMs: 450,
            delayMs: 0,
            ...layer.animation,
            ...patch,
        } as NonNullable<DesignerLayer["animation"]>;
        // Rotation 0: keyframes rotate on top of the layer's own rotation
        // (see migrateLayer in model.ts).
        const events = createDefaultEvents(0, animation);
        onChange({
            animation,
            animations: {
                ...(layer.animations ?? createDefaultAnimations(0, animation)),
                intro: createDefaultAnimations(0, animation).intro,
            },
            events: {
                ...layer.events,
                sceneEnter: events.sceneEnter,
            },
        } as Partial<DesignerLayer>);
    };

    return (
        <Stack gap={1.5} className="designer-inspector-fields">
            {assignmentSummary}
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
            <Stack gap={0.75}>
                <Typography variant="caption">反転</Typography>
                <Stack direction="row" gap={0.75}>
                    <Button
                        size="small"
                        variant={layer.flipX ? "contained" : "outlined"}
                        aria-pressed={Boolean(layer.flipX)}
                        onClick={() => onChange({flipX: !layer.flipX})}
                    >
                        左右反転
                    </Button>
                    <Button
                        size="small"
                        variant={layer.flipY ? "contained" : "outlined"}
                        aria-pressed={Boolean(layer.flipY)}
                        onClick={() => onChange({flipY: !layer.flipY})}
                    >
                        上下反転
                    </Button>
                </Stack>
            </Stack>
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
                    <Typography variant="overline">
                        {layer.asset.toLowerCase().endsWith(".svg") ? "SVG" : "PNG"}
                    </Typography>
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
                    {allowGameHistory && layer.asset.toLowerCase().endsWith(".svg") && (
                        <Stack gap={1}>
                            <FormControlLabel
                                control={
                                    <Switch
                                        size="small"
                                        checked={Boolean(layer.gameHistoryStyle)}
                                        onChange={(event) => onChange({
                                            gameHistoryStyle: event.target.checked
                                                ? createDefaultGameHistoryImageStyle()
                                                : undefined,
                                        })}
                                    />
                                }
                                label="Game-History表示"
                            />
                            {layer.gameHistoryStyle && (
                                <>
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                size="small"
                                                checked={layer.gameHistoryStyle.mode === "parts"}
                                                onChange={(event) =>
                                                    onGameHistoryPartEditingChange?.(
                                                        event.target.checked,
                                                    )}
                                            />
                                        }
                                        label="SVG内部パーツを個別指定"
                                    />
                                    <Stack gap={0.75}>
                                        <Typography variant="caption" color="text.secondary">
                                            このSVGレイヤーを選択した状態で、割り当てたいセットを押します。
                                        </Typography>
                                        <Typography variant="subtitle2">
                                            P1/P2セット1・2・共有セット3への割り当て
                                            </Typography>
                                            {GAME_HISTORY_PART_TARGETS.map((target) => {
                                                const targetLabel = gameHistoryTargetLabel(target);
                                                const assignedPart = layer.type === "image"
                                                    ? layer.gameHistoryStyle?.parts?.find((part) =>
                                                        part.path.length === 0 &&
                                                        part.role === target.role &&
                                                        part.setIndex === target.setIndex)
                                                    : undefined;
                                                const assigned = Boolean(assignedPart);
                                                return (
                                                    <Stack
                                                        key={`${target.role}-${target.setIndex}`}
                                                        direction="row"
                                                        gap={0.5}
                                                        alignItems="center"
                                                    >
                                                        <Box sx={{minWidth: 92}}>
                                                            <Typography variant="caption">
                                                                {targetLabel}
                                                            </Typography>
                                                            <Typography
                                                                variant="caption"
                                                                color={assigned ? "text.primary" : "text.secondary"}
                                                                display="block"
                                                                noWrap
                                                            >
                                                                {assigned
                                                                    ? "このSVGレイヤー"
                                                                    : "未指定"}
                                                            </Typography>
                                                        </Box>
                                                        <Button
                                                            size="small"
                                                            variant={assigned ? "contained" : "outlined"}
                                                            onClick={() => onGameHistoryPartTargetAssign?.(target)}
                                                            aria-label={`${targetLabel}にこのSVGレイヤーを指定`}
                                                        >
                                                            {assigned ? "指定済み" : "このSVGを指定"}
                                                        </Button>
                                                        <Button
                                                            size="small"
                                                            color="error"
                                                            disabled={!assigned}
                                                            onClick={() => onGameHistoryPartTargetRemove?.(target)}
                                                            aria-label={`${targetLabel}の指定を解除`}
                                                        >
                                                            解除
                                                        </Button>
                                                    </Stack>
                                                );
                                            })}
                                        <Typography variant="caption">
                                        設定済みSVG指定: {GAME_HISTORY_PART_TARGETS.filter((target) =>
                                            layer.gameHistoryStyle?.parts?.some((part) =>
                                                part.path.length === 0 &&
                                                part.role === target.role &&
                                                part.setIndex === target.setIndex)).length}
                                        </Typography>
                                        {layer.gameHistoryStyle.mode === "parts" && selectedGameHistoryPartPath && (
                                                <>
                                                    <Typography variant="caption" color="text.secondary">
                                                        選択中: SVG/{selectedGameHistoryPartPath.join("/")}
                                                    </Typography>
                                                    <TextField
                                                        select
                                                        size="small"
                                                        label="点灯条件"
                                                        value={selectedPartValue}
                                                        onChange={(event) => {
                                                            const value = event.target.value;
                                                            if (value === "") {
                                                                onGameHistoryPartRemove?.();
                                                            } else if (value === "winner") {
                                                                onGameHistoryPartRoleChange?.("winner");
                                                            } else if (value.startsWith("set:")) {
                                                                onGameHistoryPartRoleChange?.(
                                                                    "set",
                                                                    Number(value.slice(4)),
                                                                );
                                                            } else if (value.startsWith("p1-set:")) {
                                                                onGameHistoryPartRoleChange?.(
                                                                    "p1-set",
                                                                    Number(value.slice(7)),
                                                                );
                                                            } else if (value.startsWith("p2-set:")) {
                                                                onGameHistoryPartRoleChange?.(
                                                                    "p2-set",
                                                                    Number(value.slice(7)),
                                                                );
                                                            }
                                                        }}
                                                    >
                                                        <MenuItem value="">未設定</MenuItem>
                                                        {Array.from({length: GAME_HISTORY_SET_COUNT}, (_, index) => (
                                                            <MenuItem key={`p1-${index}`} value={`p1-set:${index}`}>
                                                                {gameHistoryTargetLabel({
                                                                    role: "p1-set",
                                                                    setIndex: index as 0 | 1 | 2,
                                                                })}
                                                            </MenuItem>
                                                        ))}
                                                        {Array.from({length: GAME_HISTORY_SET_COUNT}, (_, index) => (
                                                            <MenuItem key={`p2-${index}`} value={`p2-set:${index}`}>
                                                                {gameHistoryTargetLabel({
                                                                    role: "p2-set",
                                                                    setIndex: index as 0 | 1 | 2,
                                                                })}
                                                            </MenuItem>
                                                        ))}
                                                        {Array.from({length: 5}, (_, index) => (
                                                            <MenuItem key={`legacy-set-${index}`} value={`set:${index}`}>
                                                                全体順セット{index + 1}（互換）
                                                            </MenuItem>
                                                        ))}
                                                        <MenuItem value="winner">中央（勝利ランプ）</MenuItem>
                                                    </TextField>
                                                    <Button
                                                        size="small"
                                                        color="error"
                                                        variant="outlined"
                                                        disabled={!selectedGameHistoryPart}
                                                        onClick={onGameHistoryPartRemove}
                                                    >
                                                        選択パーツの設定を解除
                                                    </Button>
                                                </>
                                        )}
                                        {layer.gameHistoryStyle.mode === "parts" && !gameHistoryPartEditing && (
                                                <Typography variant="caption" color="warning.main">
                                                    SVG内部パーツを個別に指定する場合は、上のスイッチを有効にしてください。
                                                </Typography>
                                        )}
                                    </Stack>
                                    <Typography variant="caption" color="text.secondary">
                                        未取得は線、取得済みは塗りで表示します。
                                    </Typography>
                                    <NumberField
                                        label="ゲーム番号"
                                        value={layer.gameHistoryStyle.slotIndex + 1}
                                        min={1}
                                        max={5}
                                        step={1}
                                        onChange={(slotIndex) => onChange({
                                            gameHistoryStyle: {
                                                ...layer.gameHistoryStyle!,
                                                slotIndex: Math.max(0, Math.min(4,
                                                    Math.round(slotIndex) - 1)),
                                            },
                                        })}
                                    />
                                    <ColorField
                                        label="P1取得色"
                                        value={layer.gameHistoryStyle.p1Color}
                                        onChange={(p1Color) => onChange({
                                            gameHistoryStyle: {
                                                ...layer.gameHistoryStyle!,
                                                p1Color,
                                            },
                                        })}
                                    />
                                    <ColorField
                                        label="P2取得色"
                                        value={layer.gameHistoryStyle.p2Color}
                                        onChange={(p2Color) => onChange({
                                            gameHistoryStyle: {
                                                ...layer.gameHistoryStyle!,
                                                p2Color,
                                            },
                                        })}
                                    />
                                    <ColorField
                                        label="未取得線色"
                                        value={layer.gameHistoryStyle.neutralColor}
                                        onChange={(neutralColor) => onChange({
                                            gameHistoryStyle: {
                                                ...layer.gameHistoryStyle!,
                                                neutralColor,
                                            },
                                        })}
                                    />
                                    <NumberField
                                        label="未取得線幅"
                                        value={layer.gameHistoryStyle.strokeWidth}
                                        min={0}
                                        max={100}
                                        step={1}
                                        onChange={(strokeWidth) => onChange({
                                            gameHistoryStyle: {
                                                ...layer.gameHistoryStyle!,
                                                strokeWidth: Math.max(0, strokeWidth),
                                            },
                                        })}
                                    />
                                </>
                            )}
                        </Stack>
                    )}
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
                    <Typography variant="overline">
                        {layer.binding ? "動的テキスト" : "テキスト"}
                    </Typography>
                    <TextField
                        select
                        size="small"
                        label="データ対応"
                        value={layer.binding ?? ""}
                        onChange={(event) => {
                            const binding = event.target.value || undefined;
                            const events = layer.events
                                ? {
                                    ...layer.events,
                                    dataChange: layer.events.dataChange
                                        ? {
                                            ...layer.events.dataChange,
                                            watchBindings: binding ? [binding] : [],
                                        }
                                        : undefined,
                                }
                                : undefined;
                            onChange({binding, events} as Partial<DesignerLayer>);
                        }}
                    >
                        <MenuItem value="">固定テキスト</MenuItem>
                        {TEXT_BINDINGS.map((binding) => (
                            <MenuItem key={binding} value={binding}>
                                {BINDING_LABELS[binding]}
                            </MenuItem>
                        ))}
                    </TextField>
                    {layer.binding ? (
                        <Typography variant="body2">{layer.binding}</Typography>
                    ) : (
                        <TextField
                            size="small"
                            label="テキスト"
                            value={layer.text ?? ""}
                            multiline
                            onChange={(event) => onChange({
                                text: event.target.value,
                            } as Partial<DesignerLayer>)}
                        />
                    )}
                    <TextField
                        size="small"
                        label="フォント"
                        value={layer.typography.fontFamily}
                        onChange={(event) => onChange({
                            typography: {
                                ...layer.typography,
                                fontFamily: event.target.value,
                                googleFontFamily: undefined,
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
                            step={100}
                            onChange={(fontWeight) => onChange({
                                typography: {...layer.typography, fontWeight},
                            } as Partial<DesignerLayer>)}
                        />
                        <NumberField
                            label="文字間隔"
                            value={layer.typography.letterSpacing}
                            step={0.1}
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
                value={layer.animation?.preset ?? "fade"}
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
                    value={layer.animation?.durationMs ?? 450}
                    min={0}
                    onChange={(durationMs) => updateAnimation({durationMs})}
                />
                <NumberField
                    label="遅延(ms)"
                    value={layer.animation?.delayMs ?? 0}
                    min={0}
                    onChange={(delayMs) => updateAnimation({delayMs})}
                />
            </div>
        </Stack>
    );
}
