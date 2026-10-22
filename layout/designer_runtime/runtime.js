/* Autona Scoreboard Composer runtime — no build step required. */
(function () {
  "use strict";

  var project = null;
  var elements = new Map();
  var previousValues = new Map();
  var transitions = new Map();
  var hasInitialState = false;
  var latestState = null;
  var defaultEasing = {x1: 0.2, y1: 0.8, x2: 0.2, y2: 1};
  var loadedGoogleFonts = {};

  function normalizeFontFamily(value) {
    return typeof value === "string"
      ? value.trim().replace(/^['"]|['"]$/g, "") : "";
  }

  function normalizeFontWeight(value) {
    var weight = Number(value);
    if (!isFinite(weight)) return 400;
    return Math.max(100, Math.min(900, Math.round(weight / 100) * 100));
  }

  function googleFontUrl(family, weights) {
    var unique = Array.from(new Set(weights)).sort(function (a, b) { return a - b; });
    var axes = [];
    unique.forEach(function (weight) {
      axes.push("0," + weight, "1," + weight);
    });
    return "https://fonts.googleapis.com/css2?family=" +
      encodeURIComponent(family).replace(/%20/g, "+") +
      ":ital,wght@" + axes.join(";") + "&display=swap";
  }

  function ensureProjectGoogleFonts(value) {
    var requests = {};
    (value.layers || []).forEach(function (layer) {
      if (layer.type !== "data-text" || !layer.typography) return;
      var family = normalizeFontFamily(layer.typography.googleFontFamily);
      if (!family || family === "LINE Seed JP") return;
      if (!requests[family]) requests[family] = [];
      requests[family].push(normalizeFontWeight(layer.typography.fontWeight));
    });
    Object.keys(requests).forEach(function (family) {
      var weights = Array.from(new Set(requests[family])).sort(function (a, b) { return a - b; });
      var key = family + "|" + weights.join(",");
      if (loadedGoogleFonts[key]) return;
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = googleFontUrl(family, weights);
      link.dataset.autonaGoogleFont = family;
      document.head.appendChild(link);
      loadedGoogleFonts[key] = link;
    });
  }

  function showError(error) {
    var target = document.getElementById("composer-error");
    target.style.display = "block";
    target.textContent = "スコアボードを読み込めません。\n" +
      (error && error.message ? error.message : String(error));
    document.body.style.opacity = "1";
    console.error(error);
  }

  function createKeyframe(timeMs, x, y, rotation, scaleX, scaleY) {
    return {
      id: "runtime",
      timeMs: timeMs,
      x: x,
      y: y,
      rotation: rotation,
      scaleX: scaleX == null ? 1 : scaleX,
      scaleY: scaleY == null ? 1 : scaleY
    };
  }

  function legacyStartFrame(animation) {
    var distance = 80;
    if (animation.preset === "slide-left") return createKeyframe(0, -distance, 0, 0);
    if (animation.preset === "slide-right") return createKeyframe(0, distance, 0, 0);
    if (animation.preset === "slide-up") return createKeyframe(0, 0, -distance, 0);
    if (animation.preset === "slide-down") return createKeyframe(0, 0, distance, 0);
    if (animation.preset === "zoom") return createKeyframe(0, 0, 0, 0, 0.72, 0.72);
    return createKeyframe(0, 0, 0, 0);
  }

  function animationFromLegacy(animation) {
    var duration = Math.max(0, Number(animation && animation.durationMs) || 0);
    var preset = animation && animation.preset || "none";
    return {
      renderer: "webAnimations",
      durationMs: duration,
      delayMs: Math.max(0, Number(animation && animation.delayMs) || 0),
      easing: Object.assign({}, defaultEasing),
      loop: false,
      keyframes: preset === "none"
        ? [createKeyframe(0, 0, 0, 0)]
        : [legacyStartFrame({preset: preset}), createKeyframe(duration, 0, 0, 0)]
    };
  }

  function defaultUpdateAnimation() {
    return {
      renderer: "webAnimations",
      durationMs: 440,
      delayMs: 0,
      easing: Object.assign({}, defaultEasing),
      loop: false,
      keyframes: [
        createKeyframe(0, 0, 0, 0, 1, 1),
        createKeyframe(220, 0, 0, 0, 1.04, 1.04),
        createKeyframe(440, 0, 0, 0, 1, 1)
      ]
    };
  }

  function timelineFromMotion(animation, startOpacity) {
    return {
      durationMs: animation.durationMs,
      delayMs: animation.delayMs,
      easing: Object.assign({}, animation.easing),
      visualKeyframes: animation.keyframes.map(function (frame, index) {
        return Object.assign({}, frame, {
          opacity: index === 0 && animation.keyframes.length > 1
            ? startOpacity : 1
        });
      }),
      rendererActions: []
    };
  }

  function eventsFromAnimations(layer, sourceVersion) {
    var legacy = layer.animation || {preset: "fade", durationMs: 450, delayMs: 0};
    var animations = layer.animations || {
      intro: animationFromLegacy(legacy),
      update: defaultUpdateAnimation()
    };
    return {
      sceneEnter: timelineFromMotion(
        animations.intro,
        legacy.preset === "none" ? 1 : 0
      ),
      dataChange: Object.assign(
        timelineFromMotion(animations.update, sourceVersion <= 2 ? 0 : 1),
        {
          swapAtMs: sourceVersion <= 2
            ? 0 : Math.round(animations.update.durationMs / 2),
          watchBindings: layer.binding ? [layer.binding] : [],
          interruptPolicy: "coalesceLatest"
        }
      )
    };
  }

  function migrateProject(value) {
    if (!value || !Array.isArray(value.layers)) return value;
    if (value.schemaVersion !== 1 &&
        value.schemaVersion !== 2 &&
        value.schemaVersion !== 3) return value;
    var sourceVersion = value.schemaVersion;
    value.layers = value.layers.map(function (layer) {
      var legacy = layer.animation || {preset: "fade", durationMs: 450, delayMs: 0};
      if (layer.assetTransformApplied === undefined &&
          layer.type === "image" && typeof layer.asset === "string" &&
          /\.svg$/i.test(layer.asset) && layer.sourceRef) {
        layer.assetTransformApplied = true;
        layer.flipX = false;
        layer.flipY = false;
      }
      if (!layer.animations) {
        layer.animations = {
          intro: animationFromLegacy(legacy),
          update: defaultUpdateAnimation()
        };
      }
      if (sourceVersion < 3 || !layer.events) {
        layer.events = eventsFromAnimations(layer, sourceVersion);
      }
      return layer;
    });
    if (!value.id) value.id = "legacy-runtime";
    if (!Array.isArray(value.assets)) {
      // The same manifest model.ts migratedAssets() builds (and the Designer
      // saves): image layers and Game-History slots, first use wins.
      // Rendering never reads it -- layers and slots use their own paths.
      value.assets = [];
      value.layers.forEach(function (layer) {
        var paths = layer.type === "image"
          ? [layer.asset]
          : layer.type === "game-history" && Array.isArray(layer.slots)
            ? layer.slots.map(function (slot) { return slot && slot.asset; })
            : [];
        paths.forEach(function (path) {
          if (typeof path !== "string" ||
              value.assets.some(function (asset) { return asset.path === path; })) {
            return;
          }
          value.assets.push({
            id: "legacy-image-" + (value.assets.length + 1),
            kind: "image",
            path: path,
            mediaType: path.toLowerCase().endsWith(".svg")
              ? "image/svg+xml" : "image/png"
          });
        });
      });
    }
    value.schemaVersion = 3;
    return value;
  }

  function validateMotionAnimation(animation, label) {
    if (!animation || animation.renderer !== "webAnimations" ||
        !Number.isFinite(animation.durationMs) ||
        !Number.isFinite(animation.delayMs) ||
        !Array.isArray(animation.keyframes) ||
        animation.keyframes.length < 1) {
      throw new Error(label + " が不正です。");
    }
    ["x1", "y1", "x2", "y2"].forEach(function (key) {
      var value = animation.easing && animation.easing[key];
      if (!Number.isFinite(value) || value < 0 || value > 1) {
        throw new Error(label + ".easing が不正です。");
      }
    });
    animation.keyframes.forEach(function (frame, index) {
      ["timeMs", "x", "y", "rotation", "scaleX", "scaleY"].forEach(function (key) {
        if (!Number.isFinite(frame[key])) {
          throw new Error(label + ".keyframes[" + index + "]." + key + " が不正です。");
        }
      });
    });
  }

  function validateEventTimeline(timeline, label, isChange) {
    if (!timeline ||
        !Number.isFinite(timeline.durationMs) ||
        !Number.isFinite(timeline.delayMs) ||
        !Array.isArray(timeline.visualKeyframes) ||
        !timeline.visualKeyframes.length ||
        !Array.isArray(timeline.rendererActions)) {
      throw new Error(label + " が不正です。");
    }
    ["x1", "y1", "x2", "y2"].forEach(function (key) {
      var value = timeline.easing && timeline.easing[key];
      if (!Number.isFinite(value) || value < 0 || value > 1) {
        throw new Error(label + ".easing が不正です。");
      }
    });
    var previousTime = -1;
    timeline.visualKeyframes.forEach(function (frame, index) {
      ["timeMs", "x", "y", "rotation", "scaleX", "scaleY", "opacity"]
        .forEach(function (key) {
          if (!Number.isFinite(frame[key])) {
            throw new Error(label + ".visualKeyframes[" + index + "]." + key + " が不正です。");
          }
        });
      if (frame.timeMs < previousTime || frame.timeMs < 0 ||
          frame.timeMs > timeline.durationMs ||
          frame.scaleX <= 0 || frame.scaleY <= 0 ||
          frame.opacity < 0 || frame.opacity > 1) {
        throw new Error(label + ".visualKeyframes[" + index + "] が範囲外です。");
      }
      previousTime = frame.timeMs;
    });
    if (timeline.rendererActions.length) {
      throw new Error(label + ".rendererActions が不正です。");
    }
    if (isChange && (!Number.isFinite(timeline.swapAtMs) ||
        timeline.swapAtMs < 0 || timeline.swapAtMs > timeline.durationMs ||
        timeline.interruptPolicy !== "coalesceLatest" ||
        !Array.isArray(timeline.watchBindings))) {
      throw new Error(label + " の値変更設定が不正です。");
    }
  }

  function validateTypography(typography, label) {
    if (!typography || typeof typography.fontFamily !== "string" ||
        !typography.fontFamily.trim() ||
        (typography.googleFontFamily !== undefined &&
          (typeof typography.googleFontFamily !== "string" ||
            !typography.googleFontFamily.trim())) ||
        !Number.isFinite(typography.fontSize) ||
        !Number.isFinite(typography.fontWeight) ||
        !Number.isFinite(typography.letterSpacing) ||
        typeof typography.color !== "string" || !typography.color.trim() ||
        !["left", "center", "right"].includes(typography.align) ||
        typeof typography.italic !== "boolean") {
      throw new Error(label + " が不正です。");
    }
    if (typography.fontSize < 1 || typography.fontSize > 1000 ||
        typography.fontWeight < 1 || typography.fontWeight > 1000) {
      throw new Error(label + " のサイズまたは太さが範囲外です。");
    }
  }

  function validateProject(value) {
    value = migrateProject(value);
    if (!value || value.schemaVersion !== 3 || !value.id ||
        !Array.isArray(value.assets) || !Array.isArray(value.layers)) {
      throw new Error("project.json の形式またはschemaVersionが不正です。");
    }
    if (!value.canvas || value.canvas.width !== 1920 || value.canvas.height !== 1080) {
      throw new Error("キャンバスは1920×1080である必要があります。");
    }
    var ids = new Set();
    var layerTypes = new Set([
      "image", "data-text", "data-image", "game-history"
    ]);
    var assetIds = new Set();
    value.assets.forEach(function (asset) {
      if (!asset.id || assetIds.has(asset.id) ||
          asset.kind !== "image" ||
          !/^assets\/[^/\\]+$/.test(asset.path)) {
        throw new Error("asset manifestが不正です。");
      }
      assetIds.add(asset.id);
    });
    var presets = new Set([
      "none", "fade", "slide-left", "slide-right",
      "slide-up", "slide-down", "zoom"
    ]);
    value.layers.forEach(function (layer) {
      if (!layer.id || ids.has(layer.id)) {
        throw new Error("レイヤーIDが空または重複しています。");
      }
      if (!layerTypes.has(layer.type)) {
        throw new Error("未対応のレイヤーtypeです: " + layer.type);
      }
      ["x", "y", "width", "height", "rotation", "opacity", "zIndex"]
        .forEach(function (key) {
          if (!Number.isFinite(layer[key])) {
            throw new Error(layer.name + "." + key + " が数値ではありません。");
          }
        });
      if (layer.width <= 0 || layer.height <= 0 ||
          layer.opacity < 0 || layer.opacity > 1) {
        throw new Error(layer.name + " のサイズまたは透明度が不正です。");
      }
      ["flipX", "flipY"].forEach(function (key) {
        if (layer[key] !== undefined && typeof layer[key] !== "boolean") {
          throw new Error(layer.name + "." + key + " が不正です。");
        }
      });
      if (layer.assetTransformApplied !== undefined &&
          typeof layer.assetTransformApplied !== "boolean") {
        throw new Error(layer.name + ".assetTransformApplied が不正です。");
      }
      if (layer.animation && (!presets.has(layer.animation.preset) ||
          !Number.isFinite(layer.animation.durationMs) ||
          !Number.isFinite(layer.animation.delayMs))) {
        throw new Error(layer.name + " のanimationが不正です。");
      }
      validateEventTimeline(
        layer.events && layer.events.sceneEnter,
        layer.name + ".events.sceneEnter",
        false
      );
      validateEventTimeline(
        layer.events && layer.events.dataChange,
        layer.name + ".events.dataChange",
        true
      );
      if (layer.type === "image" &&
          (!/^assets\/[^/]+\.(png|svg)$/i.test(layer.asset) ||
            layer.asset.includes(".."))) {
        throw new Error(layer.name + " のassetが不正です。");
      }
      if (layer.type === "image" &&
          !value.assets.some(function (asset) {
            return asset.kind === "image" && asset.path === layer.asset;
          })) {
        throw new Error(layer.name + " のasset manifestがありません。");
      }
      if (layer.type === "image" && layer.gameHistoryStyle) {
        var gameHistoryStyle = layer.gameHistoryStyle;
        if (!/\.svg$/i.test(layer.asset) ||
            !Number.isInteger(gameHistoryStyle.slotIndex) ||
            gameHistoryStyle.slotIndex < 0 || gameHistoryStyle.slotIndex > 4 ||
            !Number.isFinite(gameHistoryStyle.strokeWidth) ||
            gameHistoryStyle.strokeWidth < 0 || gameHistoryStyle.strokeWidth > 100 ||
            !String(gameHistoryStyle.p1Color || "").trim() ||
            !String(gameHistoryStyle.p2Color || "").trim() ||
            !String(gameHistoryStyle.neutralColor || "").trim()) {
            throw new Error(layer.name + " のSVG Game-History設定が不正です。");
          }
        if (gameHistoryStyle.mode !== undefined &&
            gameHistoryStyle.mode !== "slot" && gameHistoryStyle.mode !== "parts") {
          throw new Error(layer.name + " のSVG Game-History modeが不正です。");
        }
        if (gameHistoryStyle.parts !== undefined) {
          if (!Array.isArray(gameHistoryStyle.parts) || gameHistoryStyle.parts.length > 100) {
            throw new Error(layer.name + " のSVG Game-History partsが不正です。");
          }
          var partPaths = {};
          gameHistoryStyle.parts.forEach(function (part) {
            var pathKey = part && Array.isArray(part.path)
              ? part.path.join(".") : "";
            var samePathParts = partPaths[pathKey] || [];
            var canSharePath = samePathParts.length > 0 &&
              samePathParts.every(function (existing) {
                return part && existing.role !== part.role &&
                  (existing.role === "p1-set" || existing.role === "p2-set") &&
                  (part && (part.role === "p1-set" || part.role === "p2-set")) &&
                  existing.setIndex === 2 && part && part.setIndex === 2;
              });
            if (!part || !Array.isArray(part.path) ||
                part.path.length > 64 ||
                part.path.some(function (value) {
                  return !Number.isInteger(value) || value < 0 || value > 10000;
                }) || (samePathParts.length > 0 && !canSharePath) ||
                (part.role !== "set" && part.role !== "p1-set" &&
                  part.role !== "p2-set" && part.role !== "winner") ||
                ((part.role === "set" || part.role === "p1-set" || part.role === "p2-set") &&
                  (!Number.isInteger(part.setIndex) || part.setIndex < 0 || part.setIndex > 4)) ||
                (part.role === "winner" && part.setIndex !== undefined)) {
              throw new Error(layer.name + " のSVG Game-History partsが不正です。");
            }
            samePathParts.push(part);
            partPaths[pathKey] = samePathParts;
          });
        }
      }
      if (layer.type === "game-history" && Array.isArray(layer.slots)) {
        // renderGames() reads every slot; reject a broken one here, with
        // the message model.ts gives, rather than fail mid-render.
        layer.slots.forEach(function (slot, slotIndex) {
          if (!slot || typeof slot.asset !== "string" ||
              !/^assets\/[^/\\]+\.(png|svg)$/i.test(slot.asset)) {
            throw new Error(
              layer.name + ".slots[" + slotIndex + "].assetが不正です。");
          }
        });
      }
      if (layer.type === "data-text") {
        validateTypography(layer.typography, layer.name + ".typography");
      }
      ids.add(layer.id);
    });
    return value;
  }

  function getTeam(state, side) {
    return state && state.score && state.score["1"] &&
      state.score["1"].team && state.score["1"].team[String(side)] || {};
  }

  function getPlayer(state, side) {
    var team = getTeam(state, side);
    return team.player && team.player["1"] || {};
  }

  function stateAsset(path) {
    if (typeof path !== "string" || !path.trim()) return "";
    if (/^(data:|blob:|https?:|file:)/i.test(path)) return path;
    var root = window.tshProgramRootPath || "../..";
    return root + "/" +
      path.replace(/^\.?[\\/]+/, "").replaceAll("\\", "/");
  }

  function resolveBinding(binding, state) {
    var matched = /^p([12])\.(.+)$/.exec(binding);
    if (matched) {
      var side = Number(matched[1]);
      var field = matched[2];
      var player = getPlayer(state, side);
      var team = getTeam(state, side);
      if (field === "name") return player.name || "";
      if (field === "sponsorName") {
        return state.display_options && state.display_options.show_sponsor === false
          ? "" : player.team || "";
      }
      if (field === "sponsorLogo") {
        return state.display_options && state.display_options.show_sponsor === false
          ? "" : stateAsset(player.sponsor_logo);
      }
      if (field === "score") return team.score == null ? 0 : team.score;
      if (field === "countryFlag") return stateAsset(player.country && player.country.asset);
      if (field === "seed") return Number(player.seed) > 0 ? "SEED " + player.seed : "";
      if (field === "twitter") return player.twitter || "";
      if (field === "state") return player.state && player.state.name || "";
    }
    if (binding === "tournament.logo") {
      var version = state.tournamentInfo && state.tournamentInfo.logoVersion || 0;
      var layoutRoot = window.tshLayoutRootPath || "..";
      return layoutRoot + "/logo.png?v=" + encodeURIComponent(version);
    }
    if (binding === "tournament.name") {
      return state.tournamentInfo && state.tournamentInfo.tournamentName || "";
    }
    if (binding === "match.phase") return state.score["1"].phase || "";
    if (binding === "match.round") return state.score["1"].match || "";
    if (binding === "games") return Number(state.score["1"].best_of) || 0;
    return "";
  }

  function bezierToCss(easing) {
    return "cubic-bezier(" + easing.x1 + "," + easing.y1 + "," +
      easing.x2 + "," + easing.y2 + ")";
  }

  function visualFrames(timeline, baseOpacity) {
    var duration = Math.max(1, Number(timeline.durationMs) || 1);
    return timeline.visualKeyframes
      .slice()
      .sort(function (a, b) { return a.timeMs - b.timeMs; })
      .map(function (frame) {
        return {
          offset: Math.max(0, Math.min(1, frame.timeMs / duration)),
          opacity: frame.opacity * baseOpacity,
          transform: "translate(" + frame.x + "px, " + frame.y + "px) " +
            "rotate(" + frame.rotation + "deg) " +
            "scale(" + frame.scaleX + ", " + frame.scaleY + ")"
        };
      });
  }

  function playTimeline(layer, refs, timeline) {
    if (!timeline || !timeline.visualKeyframes.length) return null;
    if (refs.animation) refs.animation.cancel();
    refs.animation = refs.content.animate(visualFrames(timeline, layer.opacity), {
      duration: Math.max(0, Number(timeline.durationMs) || 0),
      delay: Math.max(0, Number(timeline.delayMs) || 0),
      easing: bezierToCss(timeline.easing),
      iterations: 1,
      fill: "both"
    });
    return refs.animation;
  }

  function clearTimeline(refs) {
    if (refs.animation) refs.animation.cancel();
    refs.animation = null;
  }

  function createLayer(layer) {
    var element = document.createElement("section");
    element.className = "composer-layer";
    element.dataset.layerId = layer.id;
    Object.assign(element.style, {
      left: layer.x + "px",
      top: layer.y + "px",
      width: layer.width + "px",
      height: layer.height + "px",
      transform: layer.assetTransformApplied ?
        "scale(" + (layer.flipX ? -1 : 1) + ", " +
        (layer.flipY ? -1 : 1) + ")" :
        "rotate(" + layer.rotation + "deg) scale(" +
        (layer.flipX ? -1 : 1) + ", " + (layer.flipY ? -1 : 1) + ")",
      zIndex: String(layer.zIndex),
      display: layer.visible ? "block" : "none"
    });

    var content = document.createElement("div");
    content.className = "composer-content";
    content.style.opacity = String(layer.opacity);
    element.appendChild(content);

    if (layer.type === "image") {
      if (layer.gameHistoryStyle) {
        var svgGameHistory = document.createElement("div");
        svgGameHistory.className = "composer-svg-game-history";
        content.appendChild(svgGameHistory);
      } else {
        var staticImage = document.createElement("img");
        staticImage.src = layer.asset;
        staticImage.alt = "";
        staticImage.style.objectFit = layer.objectFit || "contain";
        content.appendChild(staticImage);
      }
    } else if (layer.type === "data-text") {
      var text = document.createElement("div");
      text.className = "composer-text";
      text.style.justifyContent = layer.typography.align === "left"
        ? "flex-start" : layer.typography.align === "right" ? "flex-end" : "center";
      var textContent = document.createElement("div");
      textContent.className = "composer-text-content";
      Object.assign(textContent.style, {
        color: layer.typography.color,
        fontFamily: layer.typography.fontFamily,
        fontSize: layer.typography.fontSize + "px",
        fontWeight: String(layer.typography.fontWeight),
        fontStyle: layer.typography.italic ? "italic" : "normal",
        letterSpacing: layer.typography.letterSpacing + "px",
        transformOrigin: layer.typography.align
      });
      text.appendChild(textContent);
      content.appendChild(text);
    } else if (layer.type === "data-image") {
      var dynamicImage = document.createElement("img");
      dynamicImage.alt = "";
      dynamicImage.style.objectFit = layer.objectFit || "contain";
      content.appendChild(dynamicImage);
    } else if (layer.type === "game-history") {
      var games = document.createElement("div");
      games.className = "composer-games";
      games.style.gap = layer.gap + "px";
      content.appendChild(games);
    }

    document.getElementById("scoreboard-composer").appendChild(element);
    var refs = {
      element: element,
      content: content,
      animation: null,
      svgSource: null,
      svgLoading: false,
      svgLoadFailed: false
    };
    elements.set(layer.id, refs);
  }

  function fitText(container) {
    var content = container.querySelector(".composer-text-content");
    if (!content) return;
    content.style.transform = "";
    var scale = Math.min(1, container.clientWidth / Math.max(1, content.scrollWidth));
    content.style.transform = "scaleX(" + scale + ")";
  }

  function gameSignature(layer, state) {
    var bestOf = Math.max(0, Number(resolveBinding("games", state)) || 0);
    var p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    var p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    return [bestOf, p1, p2].join(":");
  }

  function gameHistoryImageStatus(style, state) {
    var p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    var p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    if (style.slotIndex < p1) return "p1";
    if (style.slotIndex < p1 + p2) return "p2";
    return "neutral";
  }

  function safeSvgColor(value, fallback) {
    var candidate = String(value || "").trim();
    return /^[#(),.%\w\s-]+$/.test(candidate) ? candidate : fallback;
  }

  function colorizeGameHistorySvg(source, status, style) {
    if (typeof DOMParser === "undefined" ||
        typeof XMLSerializer === "undefined") return null;
    var document = new DOMParser().parseFromString(source, "image/svg+xml");
    var root = document.documentElement;
    if (!root || root.nodeName.toLowerCase() !== "svg") return null;
    root.querySelectorAll("script,foreignObject").forEach(function (element) {
      element.remove();
    });
    root.querySelectorAll("*").forEach(function (element) {
      Array.from(element.attributes).forEach(function (attribute) {
        var name = attribute.name.toLowerCase();
        var value = attribute.value.trim().toLowerCase();
        if (name.indexOf("on") === 0 ||
            ((name === "href" || name === "src") &&
              /^(https?:|\/\/|javascript:)/.test(value))) {
          element.removeAttribute(attribute.name);
        }
      });
    });
    var activeColor = status === "p1"
      ? style.p1Color
      : status === "p2" ? style.p2Color : style.neutralColor;
    var color = safeSvgColor(activeColor, "#ffffff");
    var drawableSelector = "path,circle,ellipse,line,polygon,polyline,rect,use";
    root.querySelectorAll(drawableSelector).forEach(function (element) {
      if (status === "neutral") {
        element.style.setProperty("fill", "none", "important");
        element.style.setProperty("stroke", color, "important");
        element.style.setProperty(
          "stroke-width", Math.max(0, style.strokeWidth) + "px", "important");
        element.style.setProperty("stroke-linejoin", "round", "important");
        element.style.setProperty("stroke-linecap", "round", "important");
      } else {
        element.style.setProperty("fill", color, "important");
        element.style.setProperty("stroke", "none", "important");
      }
    });
    return new XMLSerializer().serializeToString(root);
  }

  function gameHistoryImagePartStatus(part, state) {
    var p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    var p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    if (part.role === "winner") {
      var target = Math.ceil(
        Math.max(0, Number(resolveBinding("games", state)) || 0) / 2);
      var p1Won = target > 0 && p1 >= target;
      var p2Won = target > 0 && p2 >= target;
      if (p1Won === p2Won) return "neutral";
      return p1Won ? "p1" : "p2";
    }
    if (part.role === "p1-set") return part.setIndex < p1 ? "p1" : "neutral";
    if (part.role === "p2-set") return part.setIndex < p2 ? "p2" : "neutral";
    if (part.setIndex < p1) return "p1";
    if (part.setIndex < p1 + p2) return "p2";
    return "neutral";
  }

  function gameHistoryImagePartsStatus(parts, state) {
    var hasP1 = parts.some(function (part) {
      return gameHistoryImagePartStatus(part, state) === "p1";
    });
    if (hasP1) return "p1";
    var hasP2 = parts.some(function (part) {
      return gameHistoryImagePartStatus(part, state) === "p2";
    });
    return hasP2 ? "p2" : "neutral";
  }

  function findSvgElementByPath(root, path) {
    var current = root;
    for (var index = 0; index < path.length; index += 1) {
      var child = current.children.item(path[index]);
      if (!child) return null;
      current = child;
    }
    return current;
  }

  function colorizeSvgElement(element, status, style) {
    var activeColor = status === "p1"
      ? style.p1Color
      : status === "p2" ? style.p2Color : style.neutralColor;
    var color = safeSvgColor(activeColor, "#ffffff");
    var selector = "path,circle,ellipse,line,polygon,polyline,rect,use";
    var drawables = [];
    if (element.matches(selector)) drawables.push(element);
    element.querySelectorAll(selector).forEach(function (drawable) {
      drawables.push(drawable);
    });
    drawables.forEach(function (drawable) {
      if (status === "neutral") {
        drawable.style.setProperty("fill", "none", "important");
        drawable.style.setProperty("stroke", color, "important");
        drawable.style.setProperty(
          "stroke-width", Math.max(0, style.strokeWidth) + "px", "important");
        drawable.style.setProperty("stroke-linejoin", "round", "important");
        drawable.style.setProperty("stroke-linecap", "round", "important");
      } else {
        drawable.style.setProperty("fill", color, "important");
        drawable.style.setProperty("stroke", "none", "important");
      }
    });
  }

  function colorizeGameHistorySvgParts(source, style, state) {
    if (typeof DOMParser === "undefined" ||
        typeof XMLSerializer === "undefined") return null;
    var document = new DOMParser().parseFromString(source, "image/svg+xml");
    var root = document.documentElement;
    if (!root || root.nodeName.toLowerCase() !== "svg") return null;
    root.querySelectorAll("script,foreignObject").forEach(function (element) {
      element.remove();
    });
    root.querySelectorAll("*").forEach(function (element) {
      Array.from(element.attributes).forEach(function (attribute) {
        var name = attribute.name.toLowerCase();
        var value = attribute.value.trim().toLowerCase();
        if (name.indexOf("on") === 0 ||
            ((name === "href" || name === "src") &&
              /^(https?:|\/\/|javascript:)/.test(value))) {
          element.removeAttribute(attribute.name);
        }
      });
    });
    var partsByPath = {};
    (style.parts || []).forEach(function (part) {
      var pathKey = part.path.join(".");
      if (!partsByPath[pathKey]) partsByPath[pathKey] = [];
      partsByPath[pathKey].push(part);
    });
    Object.keys(partsByPath).forEach(function (pathKey) {
      var parts = partsByPath[pathKey];
      var part = parts[0];
      var element = findSvgElementByPath(root, part.path);
      if (!element) return;
      element.setAttribute("data-autona-part-path", pathKey);
      if (parts.some(function (candidate) {
        return candidate.role === "set" &&
          candidate.setIndex >= Math.max(0, Number(resolveBinding("games", state)) || 0);
      })) {
        element.setAttribute("visibility", "hidden");
        return;
      }
      element.removeAttribute("visibility");
      colorizeSvgElement(element, gameHistoryImagePartsStatus(parts, state), style);
    });
    return new XMLSerializer().serializeToString(root);
  }

  function renderSvgGameHistory(layer, container, state, refs) {
    if (!container || !layer.gameHistoryStyle) return;
    refs.svgState = state;
    var render = function (source) {
      var status = gameHistoryImageStatus(
        layer.gameHistoryStyle, refs.svgState || state);
      var markup = layer.gameHistoryStyle.mode === "parts"
        ? colorizeGameHistorySvgParts(
          source, layer.gameHistoryStyle, refs.svgState || state)
        : colorizeGameHistorySvg(source, status, layer.gameHistoryStyle);
      if (markup) {
        container.innerHTML = markup;
        return;
      }
      var fallback = document.createElement("img");
      fallback.src = layer.asset;
      fallback.alt = "";
      fallback.style.objectFit = layer.objectFit || "contain";
      container.replaceChildren(fallback);
    };
    if (refs.svgSource !== null) {
      render(refs.svgSource);
      return;
    }
    var inlineSource = layer.gameHistoryStyle.svgSource;
    if (typeof inlineSource === "string" && inlineSource) {
      refs.svgSource = inlineSource;
      render(inlineSource);
      return;
    }
    if (refs.svgLoading || refs.svgLoadFailed) return;
    refs.svgLoading = true;
    fetch(layer.asset, {cache: "no-store"})
      .then(function (response) {
        if (!response.ok) throw new Error("SVG asset not found");
        return response.text();
      })
      .then(function (source) {
        refs.svgSource = source;
        refs.svgLoading = false;
        render(source);
      })
      .catch(function () {
        refs.svgLoading = false;
        refs.svgLoadFailed = true;
        var fallback = document.createElement("img");
        fallback.src = layer.asset;
        fallback.alt = "";
        fallback.style.objectFit = layer.objectFit || "contain";
        container.replaceChildren(fallback);
      });
  }

  function renderGames(layer, container, state) {
    var bestOf = Math.max(0, Number(resolveBinding("games", state)) || 0);
    var p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    var p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    container.replaceChildren();
    if (Array.isArray(layer.slots)) {
      container.classList.add("composer-games-custom");
      layer.slots.slice(0, Math.min(5, bestOf)).forEach(function (slot, index) {
        var status = index < p1
          ? "p1"
          : index < p1 + p2 ? "p2" : "neutral";
        var item = document.createElement("div");
        item.className = "composer-game-slot composer-game-slot-" + status;
        Object.assign(item.style, {
          position: "absolute",
          left: slot.x + "px",
          top: slot.y + "px",
          width: slot.width + "px",
          height: slot.height + "px",
          opacity: String((slot.opacity == null ? 1 : slot.opacity) * (
            status === "neutral" ? 0.35 : 1)),
          border: layer.borderWidth + "px solid " + (
            status === "p1" ? layer.p1Color
              : status === "p2" ? layer.p2Color : layer.borderColor),
          borderRadius: layer.radius + "px",
          boxSizing: "border-box",
          filter: status === "neutral" ? "grayscale(1)" : "none"
        });
        var image = document.createElement("img");
        image.src = slot.asset;
        image.alt = "";
        image.style.objectFit = "contain";
        item.appendChild(image);
        container.appendChild(item);
      });
      return;
    }
    container.classList.remove("composer-games-custom");
    for (var index = 0; index < bestOf; index += 1) {
      var box = document.createElement("div");
      var color = index < p1
        ? layer.p1Color
        : index < p1 + p2 ? layer.p2Color : layer.neutralColor;
      Object.assign(box.style, {
        flex: "1",
        height: "100%",
        background: color,
        border: layer.borderWidth + "px solid " + layer.borderColor,
        borderRadius: layer.radius + "px",
        boxSizing: "border-box"
      });
      container.appendChild(box);
    }
  }

  function layerSignature(layer, state) {
    var watched = layer.events && layer.events.dataChange &&
      layer.events.dataChange.watchBindings || [];
    if (watched.length) {
      return JSON.stringify(watched.map(function (binding) {
        return resolveBinding(binding, state);
      }));
    }
    if (layer.type === "data-text") {
      return layer.binding
        ? String(resolveBinding(layer.binding, state))
        : String(layer.text || "");
    }
    if (layer.type === "data-image") {
      return String(resolveBinding(layer.binding, state));
    }
    if (layer.type === "image" && layer.gameHistoryStyle) {
      return gameSignature(layer, state);
    }
    if (layer.type === "game-history") return gameSignature(layer, state);
    return "";
  }

  function commitLayerState(layer, refs, state, signature) {
    if (layer.type === "image" && layer.gameHistoryStyle) {
      renderSvgGameHistory(
        layer,
        refs.content.querySelector(".composer-svg-game-history"),
        state,
        refs,
      );
    } else if (layer.type === "data-text") {
      refs.content.querySelector(".composer-text-content").textContent =
        layer.binding
          ? String(resolveBinding(layer.binding, state))
          : String(layer.text || "");
      fitText(refs.content.querySelector(".composer-text"));
    } else if (layer.type === "data-image") {
      var source = String(resolveBinding(layer.binding, state));
      refs.content.querySelector("img").src = source;
      refs.content.style.visibility = source ? "visible" : "hidden";
    } else if (layer.type === "game-history") {
      renderGames(layer, refs.content.querySelector(".composer-games"), state);
    }
    previousValues.set(layer.id, signature);
  }

  function cancelTransition(layerId) {
    var transition = transitions.get(layerId);
    if (!transition) return;
    transition.cancel();
    transitions.delete(layerId);
  }

  function startDataTransition(layer, refs, transition) {
    var timeline = layer.events.dataChange;
    var cancelled = false;
    var swapTimer;
    var finishTimer;
    clearTimeline(refs);
    playTimeline(layer, refs, timeline);

    function cancel() {
      cancelled = true;
      window.clearTimeout(swapTimer);
      window.clearTimeout(finishTimer);
      clearTimeline(refs);
    }

    transition.swapped = false;
    transition.cancel = cancel;
    transitions.set(layer.id, transition);
    swapTimer = window.setTimeout(function () {
      if (cancelled || transitions.get(layer.id) !== transition) return;
      transition.swapped = true;
      transition.displayedSignature = transition.pendingSignature;
      commitLayerState(
        layer, refs, transition.pendingState, transition.pendingSignature
      );
    }, timeline.delayMs + timeline.swapAtMs);
    finishTimer = window.setTimeout(function () {
      if (cancelled || transitions.get(layer.id) !== transition) return;
      transitions.delete(layer.id);
      if (transition.pendingSignature !== transition.displayedSignature) {
        startDataTransition(layer, refs, transition);
      }
    }, timeline.delayMs + timeline.durationMs);
  }

  function updateLayer(layer, state, firstRun) {
    var refs = elements.get(layer.id);
    if (!refs || !layer.visible) return;
    if (layer.type === "image" && !layer.gameHistoryStyle) return;
    var signature = layerSignature(layer, state);
    if (firstRun || !previousValues.has(layer.id)) {
      commitLayerState(layer, refs, state, signature);
      return;
    }
    var transition = transitions.get(layer.id);
    if (transition) {
      if (transition.pendingSignature === signature) return;
      transition.pendingSignature = signature;
      transition.pendingState = state;
      if (!transition.swapped) return;
      transition.cancel();
    } else if (previousValues.get(layer.id) === signature) {
      return;
    }
    startDataTransition(layer, refs, {
      displayedSignature: previousValues.get(layer.id),
      pendingSignature: signature,
      pendingState: state,
      swapped: false,
      cancel: function () {}
    });
  }

  function renderState(state, firstRun) {
    if (!project || !state || !state.score || !state.score["1"]) return;
    latestState = state;
    project.layers.forEach(function (layer) {
      updateLayer(layer, state, firstRun);
    });
    hasInitialState = true;
  }

  function buildProject() {
    var root = document.getElementById("scoreboard-composer");
    root.style.backgroundColor = project.canvas.backgroundColor || "transparent";
    project.layers
      .slice()
      .sort(function (a, b) { return a.zIndex - b.zIndex; })
      .forEach(createLayer);
  }

  function loadProject() {
    var path = "./project.json";
    return fetch(path, {cache: "no-store"})
      .then(function (response) {
        if (!response.ok) throw new Error("project.json が見つかりません。");
        return response.json();
      })
      .then(validateProject);
  }

  function currentProjectSlug() {
    var matched = /\/layout\/custom\/([^/]+)\/index\.html$/.exec(
      window.location.pathname
    );
    if (!matched) return "";
    try {
      return decodeURIComponent(matched[1]);
    } catch (error) {
      return matched[1];
    }
  }

  function watchDesignerSaves() {
    var socket = window.tshSocket;
    var slug = currentProjectSlug();
    if (!socket || !slug) return;
    socket.on("designer_project_saved", function (saved) {
      if (saved && saved.slug === slug) {
        window.location.reload();
      }
    });
  }

  // Contract-test hook, inert unless the host defines
  // window.__autonaDesignerContractTest first (no shipped page does): hands
  // out what loadProject() applies to ./project.json, so
  // tests/test_designer_runtime_contract.py can check that this migrates
  // exactly like web/src/ScoreboardDesigner/model.ts.
  if (window.__autonaDesignerContractTest) {
    window.__autonaDesignerContractTest.readProject = validateProject;
  }

  loadProject()
    .then(function (loaded) {
      project = loaded;
      ensureProjectGoogleFonts(project);
      buildProject();

      Start = async function () {
        document.body.style.opacity = "1";
        project.layers.forEach(function (layer) {
          var refs = elements.get(layer.id);
          if (refs && layer.visible) {
            cancelTransition(layer.id);
            clearTimeline(refs);
            playTimeline(layer, refs, layer.events.sceneEnter);
          }
        });
      };

      Update = async function (event) {
        renderState(event.data, !hasInitialState);
      };

      window.AutonaDesignerRuntime = {
        hide: function () {
          project.layers.forEach(function (layer) {
            cancelTransition(layer.id);
            var refs = elements.get(layer.id);
            if (!refs) return;
            clearTimeline(refs);
          });
        },
        show: function () {
          Start();
        }
      };

      return LoadEverything().then(watchDesignerSaves);
    })
    .catch(showError);
})();
