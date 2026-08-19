import React from "react";
import {
    Button,
    ButtonGroup,
    Divider,
    MenuItem,
    Select,
    Slider,
    Stack,
    Typography,
} from "@mui/material";
import Add from "@mui/icons-material/Add";
import Delete from "@mui/icons-material/Delete";
import PlayArrow from "@mui/icons-material/PlayArrow";
import {createVisualKeyframe} from "./model";
import {
    BINDINGS,
    ChangeTimeline,
    DesignerLayer,
    EventTimeline,
    LayerEvents,
    VisualKeyframe,
} from "./types";
import BezierEditor from "./BezierEditor";
import NumberField from "./NumberField";

type TrackKey = keyof LayerEvents;

interface Props {
    layer: DesignerLayer | null;
    trackKey: TrackKey;
    onTrackKeyChange: (key: TrackKey) => void;
    onChange: (patch: Partial<DesignerLayer>) => void;
    onPreview: () => void;
}

function sortedFrames(track: EventTimeline): VisualKeyframe[] {
    return [...track.visualKeyframes].sort((a, b) => a.timeMs - b.timeMs);
}

export default function AnimationPanel({
    layer,
    trackKey,
    onTrackKeyChange,
    onChange,
    onPreview,
}: Props) {
    const [selectedFrameId, setSelectedFrameId] = React.useState<string | null>(null);

    React.useEffect(() => {
        setSelectedFrameId(null);
    }, [layer?.id, trackKey]);

    if (!layer) {
        return (
            <Stack gap={1}>
                <Typography color="text.secondary">
                    レイヤーを選択するとアニメーションを編集できます。
                </Typography>
            </Stack>
        );
    }

    const track = layer.events[trackKey];
    if (!track) {
        return (
            <Typography color="error">
                このイベントのタイムラインがありません。
            </Typography>
        );
    }
    const frames = sortedFrames(track);
    const selectedFrame = frames.find((frame) => frame.id === selectedFrameId) ?? frames[0];

    const updateTrack = (patch: Partial<EventTimeline | ChangeTimeline>) => {
        onChange({
            events: {
                ...layer.events,
                [trackKey]: {...track, ...patch},
            },
        } as Partial<DesignerLayer>);
    };

    const updateFrame = (id: string, patch: Partial<VisualKeyframe>) => {
        updateTrack({
            visualKeyframes: track.visualKeyframes
                .map((frame) => frame.id === id ? {...frame, ...patch} : frame)
                .sort((a, b) => a.timeMs - b.timeMs),
        });
    };

    const addFrame = () => {
        const midpoint = Math.round(track.durationMs / 2);
        const frame = createVisualKeyframe(midpoint, 0, 0, 0, 1, 1, 1);
        updateTrack({
            visualKeyframes: [...track.visualKeyframes, frame]
                .sort((a, b) => a.timeMs - b.timeMs),
        });
        setSelectedFrameId(frame.id);
    };

    const deleteFrame = () => {
        if (!selectedFrame || track.visualKeyframes.length <= 1) return;
        updateTrack({
            visualKeyframes: track.visualKeyframes
                .filter((frame) => frame.id !== selectedFrame.id),
        });
        setSelectedFrameId(null);
    };

    return (
        <Stack gap={1.5} className="designer-animation-panel">
            <Stack direction="row" gap={1}>
                <Select
                    size="small"
                    sx={{flex: 1, minWidth: 0}}
                    value={trackKey}
                    onChange={(event) => onTrackKeyChange(event.target.value as TrackKey)}
                    inputProps={{"aria-label": "アニメーショントラック"}}
                >
                    <MenuItem value="sceneEnter">シーン表示時</MenuItem>
                    <MenuItem value="dataChange">データ変更時</MenuItem>
                </Select>
                <Button size="small" startIcon={<PlayArrow />} onClick={onPreview}>
                    再生
                </Button>
            </Stack>

            <div className="designer-field-grid">
                <NumberField
                    label="時間(ms)"
                    value={track.durationMs}
                    min={0}
                    onChange={(durationMs) => updateTrack({durationMs: Math.max(0, durationMs)})}
                />
                <NumberField
                    label="遅延(ms)"
                    value={track.delayMs}
                    min={0}
                    onChange={(delayMs) => updateTrack({delayMs: Math.max(0, delayMs)})}
                />
            </div>

            {trackKey === "dataChange" && (
                <>
                    <NumberField
                        label="値の差し替え時刻(ms)"
                        value={(track as ChangeTimeline).swapAtMs}
                        min={0}
                        onChange={(swapAtMs) => updateTrack({
                            swapAtMs: Math.max(0, Math.min(track.durationMs, swapAtMs)),
                        } as Partial<ChangeTimeline>)}
                    />
                    <Select
                        size="small"
                        multiple
                        value={(track as ChangeTimeline).watchBindings}
                        onChange={(event) => updateTrack({
                            watchBindings: typeof event.target.value === "string"
                                ? event.target.value.split(",")
                                : event.target.value,
                        } as Partial<ChangeTimeline>)}
                        inputProps={{"aria-label": "変更監視データ"}}
                    >
                        {BINDINGS.map((binding) => (
                            <MenuItem key={binding} value={binding}>
                                {binding}
                            </MenuItem>
                        ))}
                    </Select>
                </>
            )}

            <Stack gap={0.75}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="caption">タイムライン</Typography>
                    <ButtonGroup size="small">
                        <Button startIcon={<Add />} onClick={addFrame}>追加</Button>
                        <Button
                            startIcon={<Delete />}
                            disabled={!selectedFrame || track.visualKeyframes.length <= 1}
                            onClick={deleteFrame}
                        >
                            削除
                        </Button>
                    </ButtonGroup>
                </Stack>
                <div className="designer-timeline">
                    {frames.map((frame) => (
                        <button
                            type="button"
                            key={frame.id}
                            className={frame.id === selectedFrame?.id ? "selected" : ""}
                            style={{
                                left: `${track.durationMs ? frame.timeMs / track.durationMs * 100 : 0}%`,
                            }}
                            aria-label={`${frame.timeMs}ms キーフレーム`}
                            onClick={() => setSelectedFrameId(frame.id)}
                        />
                    ))}
                </div>
            </Stack>

            {selectedFrame && (
                <Stack gap={1}>
                    <Typography variant="overline">キーフレーム</Typography>
                    <Slider
                        size="small"
                        min={0}
                        max={Math.max(0, track.durationMs)}
                        step={1}
                        value={selectedFrame.timeMs}
                        aria-label="キーフレーム時刻"
                        onChange={(_, timeMs) =>
                            updateFrame(selectedFrame.id, {timeMs: timeMs as number})}
                    />
                    <div className="designer-field-grid">
                        <NumberField
                            label="X移動"
                            value={selectedFrame.x}
                            onChange={(x) => updateFrame(selectedFrame.id, {x})}
                        />
                        <NumberField
                            label="Opacity"
                            value={selectedFrame.opacity}
                            min={0}
                            max={1}
                            step={0.01}
                            onChange={(opacity) =>
                                updateFrame(selectedFrame.id, {
                                    opacity: Math.max(0, Math.min(1, opacity)),
                                })}
                        />
                        <NumberField
                            label="Y移動"
                            value={selectedFrame.y}
                            onChange={(y) => updateFrame(selectedFrame.id, {y})}
                        />
                        <NumberField
                            label="回転"
                            value={selectedFrame.rotation}
                            onChange={(rotation) => updateFrame(selectedFrame.id, {rotation})}
                        />
                        <NumberField
                            label="Scale X"
                            value={selectedFrame.scaleX}
                            min={0.01}
                            step={0.01}
                            onChange={(scaleX) =>
                                updateFrame(selectedFrame.id, {scaleX: Math.max(0.01, scaleX)})}
                        />
                        <NumberField
                            label="Scale Y"
                            value={selectedFrame.scaleY}
                            min={0.01}
                            step={0.01}
                            onChange={(scaleY) =>
                                updateFrame(selectedFrame.id, {scaleY: Math.max(0.01, scaleY)})}
                        />
                    </div>
                </Stack>
            )}

            <Divider />
            <BezierEditor
                value={track.easing}
                onChange={(easing) => updateTrack({easing})}
            />
        </Stack>
    );
}
