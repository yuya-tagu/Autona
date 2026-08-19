const AUTONA_CANVAS_WIDTH = 1920;
const AUTONA_CANVAS_HEIGHT = 1080;

const BINDINGS = [
  "p1.name",
  "p1.sponsorName",
  "p1.sponsorLogo",
  "p1.score",
  "p1.countryFlag",
  "p1.seed",
  "p1.twitter",
  "p1.state",
  "p2.name",
  "p2.sponsorName",
  "p2.sponsorLogo",
  "p2.score",
  "p2.countryFlag",
  "p2.seed",
  "p2.twitter",
  "p2.state",
  "tournament.logo",
  "tournament.name",
  "match.phase",
  "match.round",
  "games",
];

const IMAGE_BINDINGS = new Set([
  "p1.sponsorLogo",
  "p1.countryFlag",
  "p2.sponsorLogo",
  "p2.countryFlag",
  "tournament.logo",
]);

function bindingFromLayerName(name) {
  const candidate = String(name || "").normalize("NFKC").trim().toLowerCase();
  return BINDINGS.includes(candidate) ? candidate : null;
}

function modeFromBinding(binding) {
  if (!binding) return null;
  if (IMAGE_BINDINGS.has(binding)) return "data-image";
  if (binding === "games") return "game-history";
  return "data-text";
}

figma.showUI(__html__, { width: 480, height: 620, themeColors: true });

function sanitizedName(value, fallback) {
  const normalized = String(value || fallback || "layer")
    .normalize("NFKC")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return normalized || fallback || "layer";
}

function uniqueAssetPath(name, format, stableId, usedPaths) {
  const baseName = sanitizedName(name, "layer").replace(/\.(png|svg)$/i, "");
  const base = `${baseName}-${sanitizedName(stableId, "layer")}`;
  const extension = format.toLowerCase();
  let candidate = `assets/${base}.${extension}`;
  let index = 2;
  while (usedPaths.has(candidate)) {
    candidate = `assets/${base}-${index}.${extension}`;
    index += 1;
  }
  usedPaths.add(candidate);
  return candidate;
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode.apply(null, Array.from(chunk));
  }
  return figma.base64Encode
    ? figma.base64Encode(bytes)
    : btoa(binary);
}

function findCanvasFrame(node) {
  let current = node.parent;
  while (current && current.type !== "PAGE") {
    if (
      "width" in current &&
      "height" in current &&
      Math.round(current.width) === AUTONA_CANVAS_WIDTH &&
      Math.round(current.height) === AUTONA_CANVAS_HEIGHT
    ) {
      return current;
    }
    current = current.parent;
  }
  return null;
}

function canvasFrameForNode(node) {
  return isCanvasFrame(node) ? node : findCanvasFrame(node);
}

function sourceDocumentId() {
  const fileId = typeof figma.fileKey === "string" && figma.fileKey
    ? figma.fileKey
    : figma.root && figma.root.id ? figma.root.id : "current-file";
  return `figma-${String(fileId).replace(/[^a-zA-Z0-9_-]+/g, "-")}`;
}

function isCanvasFrame(node) {
  return (
    node &&
    "width" in node &&
    "height" in node &&
    Math.round(node.width) === AUTONA_CANVAS_WIDTH &&
    Math.round(node.height) === AUTONA_CANVAS_HEIGHT
  );
}

function isTransferableNode(node) {
  return Boolean(
    node &&
    node.visible !== false &&
    node.absoluteBoundingBox &&
    "exportAsync" in node
  );
}

function isVectorNode(node) {
  return Boolean(node && new Set([
    "VECTOR",
    "BOOLEAN",
    "BOOLEAN_OPERATION",
    "ELLIPSE",
    "LINE",
    "POLYGON",
    "RECTANGLE",
    "STAR",
  ]).has(node.type));
}

function hasChildren(node) {
  return Boolean(node && "children" in node && Array.isArray(node.children));
}

function collectArtboardElements(node, result, seen) {
  if (!node || node.visible === false) return;
  if (!isVectorNode(node) && hasChildren(node) && node.children.length) {
    node.children.forEach((child) => collectArtboardElements(child, result, seen));
    return;
  }
  if (!isTransferableNode(node) || seen.has(node.id)) return;
  seen.add(node.id);
  result.push(node);
}

function collectSelectedTransferableNodes() {
  const selected = figma.currentPage.selection;
  const result = [];
  const seen = new Set();
  for (const node of selected) {
    if (isCanvasFrame(node)) {
      collectArtboardElements(node, result, seen);
      continue;
    }
    if (!isTransferableNode(node) || seen.has(node.id)) continue;
    seen.add(node.id);
    result.push(node);
  }
  return result;
}

function groupPathForNode(node) {
  const names = [];
  let current = node && node.parent;
  while (current && current.type !== "PAGE") {
    if (current.type === "GROUP") {
      const name = String(current.name || "").trim();
      if (name) names.unshift(name);
    }
    current = current.parent;
  }
  return names;
}

function displayNameForNode(node) {
  const name = String(node && node.name || "layer").trim() || "layer";
  const groups = groupPathForNode(node);
  return groups.length ? `${groups.join(" / ")} / ${name}` : name;
}

function layerBounds(node, frame) {
  if (!node.absoluteBoundingBox || !frame.absoluteBoundingBox) {
    throw new Error(`${node.name}: 座標を取得できません。`);
  }
  const bounds = node.absoluteBoundingBox;
  const frameBounds = frame.absoluteBoundingBox;
  return {
    x: Math.round(bounds.x - frameBounds.x),
    y: Math.round(bounds.y - frameBounds.y),
    width: Math.max(1, Math.round(bounds.width)),
    height: Math.max(1, Math.round(bounds.height)),
  };
}

function layerTransform(node) {
  const matrix = node && node.relativeTransform;
  if (!matrix || !Array.isArray(matrix[0]) || !Array.isArray(matrix[1])) {
    return {
      rotation: typeof node.rotation === "number" ? node.rotation : 0,
      flipX: false,
      flipY: false,
    };
  }

  const a = Number(matrix[0][0]);
  const b = Number(matrix[0][1]);
  const c = Number(matrix[1][0]);
  const d = Number(matrix[1][1]);
  if (![a, b, c, d].every(Number.isFinite)) {
    return {rotation: 0, flipX: false, flipY: false};
  }

  const reflected = a * d - b * c < -0.0001;
  // For a reflection, use a canonical horizontal flip and solve the
  // remaining rotation so that the CSS rotate(...) scaleX(-1) pair
  // reproduces the original matrix. This also handles a layer that was
  // rotated before it was flipped.
  const rotationRadians = reflected
    ? Math.atan2(-b, -a)
    : Math.atan2(b, a);
  const rotation = rotationRadians * 180 / Math.PI;
  return {
    rotation: Math.round(rotation * 100) / 100,
    flipX: reflected,
    flipY: false,
  };
}

function cssFontFamily(family) {
  const escaped = String(family)
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"');
  return `"${escaped}", sans-serif`;
}

function fontWeightFromStyle(style) {
  const normalized = String(style || "").toLowerCase().replace(/[\s_-]+/g, "");
  if (/thin|hairline/.test(normalized)) return 100;
  if (/extralight|ultralight/.test(normalized)) return 200;
  if (/light/.test(normalized)) return 300;
  if (/semibold|demibold/.test(normalized)) return 600;
  if (/extrabold|ultrabold/.test(normalized)) return 800;
  if (/black|heavy/.test(normalized)) return 900;
  if (/medium/.test(normalized)) return 500;
  if (/bold/.test(normalized)) return 700;
  return 400;
}

function colorToHex(color) {
  if (!color || typeof color !== "object") return "#ffffff";
  const channels = [color.r, color.g, color.b];
  if (!channels.every((channel) => typeof channel === "number" &&
      Number.isFinite(channel))) {
    return "#ffffff";
  }
  return `#${channels.map((channel) => Math.round(Math.max(0, Math.min(1, channel)) * 255)
    .toString(16).padStart(2, "0")).join("")}`;
}

function textFillColor(node) {
  if (!node || !Array.isArray(node.fills)) return "#ffffff";
  const solid = node.fills.find((paint) => paint && paint.visible !== false &&
    paint.type === "SOLID");
  return solid ? colorToHex(solid.color) : "#ffffff";
}

function textAlign(node, fallback) {
  const align = String(node && node.textAlignHorizontal || "").toUpperCase();
  if (align === "CENTER") return "center";
  if (align === "RIGHT" || align === "JUSTIFIED") return "right";
  return fallback || "left";
}

function letterSpacingInPixels(node, fontSize) {
  const letterSpacing = node && node.letterSpacing;
  if (!letterSpacing || typeof letterSpacing !== "object" ||
      typeof letterSpacing.value !== "number" ||
      !Number.isFinite(letterSpacing.value)) return 0;
  if (letterSpacing.unit === "PERCENT") {
    return Math.round(fontSize * letterSpacing.value / 100 * 100) / 100;
  }
  return Math.round(letterSpacing.value * 100) / 100;
}

function textTypography(node, base, fallbackAlign) {
  const fontName = node && typeof node.fontName === "object"
    ? node.fontName : null;
  const family = fontName && typeof fontName.family === "string" && fontName.family
    ? fontName.family : "LINE Seed JP";
  const style = fontName && typeof fontName.style === "string"
    ? fontName.style : "Regular";
  const fontSize = typeof node.fontSize === "number"
    ? Math.max(8, Math.min(160, Math.round(node.fontSize)))
    : Math.max(8, Math.round(base.height * 0.72));
  return {
    fontFamily: cssFontFamily(family),
    googleFontFamily: family,
    fontSize,
    fontWeight: fontWeightFromStyle(style),
    color: textFillColor(node),
    align: textAlign(node, fallbackAlign),
    italic: /italic|oblique/i.test(style),
    letterSpacing: letterSpacingInPixels(node, fontSize),
  };
}

function baseLayer(node, frame, zIndex) {
  const bounds = layerBounds(node, frame);
  const transform = layerTransform(node);
  return {
    id: `figma-${node.id.replace(/[^a-zA-Z0-9_-]+/g, "-")}`,
    name: node.name || `Layer ${zIndex}`,
    x: bounds.x,
    y: bounds.y,
    width: bounds.width,
    height: bounds.height,
    rotation: transform.rotation,
    flipX: transform.flipX,
    flipY: transform.flipY,
    opacity: typeof node.opacity === "number" ? node.opacity : 1,
    zIndex,
    visible: node.visible !== false,
    sourceRef: {
      sourceDocumentId: sourceDocumentId(),
      nodeId: node.id,
    },
    animation: {
      preset: "fade",
      durationMs: 450,
      delayMs: 0,
    },
  };
}

function createDataLayer(node, frame, zIndex, binding) {
  const base = baseLayer(node, frame, zIndex);
  if (binding === "games") {
    return Object.assign({}, base, {
      type: "game-history",
      binding,
      gap: 10,
      neutralColor: "#252524",
      p1Color: "#dc143c",
      p2Color: "#3d8be8",
      borderColor: "#f0eae0",
      borderWidth: 2,
      radius: 8,
    });
  }
  if (IMAGE_BINDINGS.has(binding)) {
    return Object.assign({}, base, {
      type: "data-image",
      binding,
      objectFit: "contain",
    });
  }
  return Object.assign({}, base, {
    type: "data-text",
    binding,
    typography: textTypography(
      node, base, binding.startsWith("p2.") ? "right" : "left"),
  });
}

function getSelectionSummary() {
  return collectSelectedTransferableNodes().map((node) => {
    const frame = canvasFrameForNode(node);
    const suggestedBinding = bindingFromLayerName(node.name);
    return {
      id: node.id,
      name: node.name || "layer",
      displayName: displayNameForNode(node),
      type: node.type,
      isVector: isVectorNode(node),
      isText: node.type === "TEXT",
      suggestedBinding,
      suggestedMode: modeFromBinding(suggestedBinding),
      hasCanvasFrame: Boolean(frame),
      frameName: frame ? frame.name : "",
    };
  });
}

async function exportSvg(node) {
  if (node.type !== "BOOLEAN" && node.type !== "BOOLEAN_OPERATION") {
    return node.exportAsync({ format: "SVG" });
  }
  if (typeof figma.flatten !== "function" || typeof node.clone !== "function") {
    return node.exportAsync({ format: "SVG" });
  }
  const clone = node.clone();
  let vector = null;
  try {
    vector = figma.flatten([clone]);
    return await vector.exportAsync({ format: "SVG" });
  } finally {
    if (vector && typeof vector.remove === "function") {
      vector.remove();
    } else if (clone && typeof clone.remove === "function") {
      clone.remove();
    }
  }
}

async function buildPayload(config) {
  const selected = collectSelectedTransferableNodes();
  if (!selected.length) {
    throw new Error("Figmaで移植したいLayerを選択してください。");
  }
  const byId = new Map(selected.map((node) => [node.id, node]));
  const usedAssetPaths = new Set();
  const layers = [];
  const assets = {};
  let zIndex = 1;

  for (const item of config.layers || []) {
    const node = byId.get(item.id);
    if (!node || item.mode === "ignore") continue;
    const frame = canvasFrameForNode(node);
    if (!frame) {
      throw new Error(`${node.name}: 直近の親Frameが1920x1080ではありません。`);
    }

    if (item.mode === "image" || item.mode === "svg") {
      if (!("exportAsync" in node)) {
        throw new Error(`${node.name}: 画像として書き出せません。`);
      }
      const format = item.mode === "svg" ? "SVG" : "PNG";
      const mediaType = format === "SVG" ? "image/svg+xml" : "image/png";
      const assetPath = uniqueAssetPath(
        node.name, format, node.id, usedAssetPaths);
      const bytes = format === "SVG"
        ? await exportSvg(node)
        : await node.exportAsync({ format });
      assets[assetPath] = `data:${mediaType};base64,${bytesToBase64(bytes)}`;
      // PNG/SVG exports already contain the node's Figma rotation and
      // reflection. Keep the exported pixels as the source of truth instead
      // of applying the same transform again around the Autona layer center.
      layers.push(Object.assign({}, baseLayer(node, frame, zIndex), {
        type: "image",
        asset: assetPath,
        objectFit: "contain",
        assetTransformApplied: true,
        rotation: 0,
        flipX: false,
        flipY: false,
      }));
      zIndex += 1;
      continue;
    }

    const binding = BINDINGS.includes(item.binding)
      ? item.binding : bindingFromLayerName(node.name);
    if (!binding) {
      throw new Error(`${node.name}: bindingが不正です。`);
    }
    if (item.mode === "data-text" && (IMAGE_BINDINGS.has(binding) || binding === "games")) {
      throw new Error(`${node.name}: テキストにできないbindingです。`);
    }
    if (item.mode === "data-image" && !IMAGE_BINDINGS.has(binding)) {
      throw new Error(`${node.name}: 画像にできないbindingです。`);
    }
    if (item.mode === "game-history" && binding !== "games") {
      throw new Error(`${node.name}: ゲーム履歴にできないbindingです。`);
    }
    layers.push(createDataLayer(node, frame, zIndex, binding));
    zIndex += 1;
  }

  if (!layers.length) {
    throw new Error("送信対象のLayerがありません。");
  }

  const projectName = config.projectName || "Figma Scoreboard";
  const sourceId = sourceDocumentId();
  const snapshot = {};
  layers.forEach((layer) => {
    if (!layer.sourceRef) return;
    snapshot[layer.sourceRef.nodeId] = {
      name: layer.name,
      x: layer.x,
      y: layer.y,
      width: layer.width,
      height: layer.height,
      rotation: layer.rotation,
      flipX: layer.flipX,
      flipY: layer.flipY,
      opacity: layer.opacity,
      zIndex: layer.zIndex,
      visible: layer.visible,
    };
  });

  return {
    project: {
      schemaVersion: 1,
      name: projectName,
      canvas: {
        width: AUTONA_CANVAS_WIDTH,
        height: AUTONA_CANVAS_HEIGHT,
        backgroundColor: "transparent",
      },
      sources: [{
        id: sourceId,
        provider: "figma",
        documentId: sourceId,
        displayName: projectName,
        fingerprint: Object.keys(snapshot).sort().join("|"),
        importedAt: new Date().toISOString(),
        snapshot,
        partial: true,
      }],
      layers,
    },
    assets,
  };
}

figma.ui.onmessage = async (message) => {
  if (message.type === "ready" || message.type === "refresh-selection") {
    postSelectionSummary();
    return;
  }

  if (message.type === "build-payload") {
    try {
      const payload = await buildPayload(message.config || {});
      figma.ui.postMessage({ type: "payload-ready", payload });
    } catch (error) {
      figma.ui.postMessage({
        type: "error",
        message: error && error.message ? error.message : String(error),
      });
    }
    return;
  }

  if (message.type === "select-layer") {
    try {
      const node = figma.getNodeById(message.id);
      if (!node || node.removed || node.type === "DOCUMENT" || node.type === "PAGE") {
        throw new Error("指定されたFigma Layerが見つかりません。");
      }
      figma.currentPage.selection = [node];
      figma.viewport.scrollAndZoomIntoView([node]);
      figma.ui.postMessage({type: "layer-focused", id: node.id});
    } catch (error) {
      figma.ui.postMessage({
        type: "error",
        message: error && error.message ? error.message : String(error),
      });
    }
    return;
  }

  if (message.type === "notify") {
    figma.notify(message.message);
    return;
  }

  if (message.type === "close") {
    figma.closePlugin();
  }
};

function postSelectionSummary() {
  const selectionIds = figma.currentPage.selection.map((node) => node.id);
  figma.ui.postMessage({
    type: "selection",
    bindings: BINDINGS,
    imageBindings: Array.from(IMAGE_BINDINGS),
    selectionIds,
    layers: getSelectionSummary(),
  });
}

figma.on("selectionchange", postSelectionSummary);
