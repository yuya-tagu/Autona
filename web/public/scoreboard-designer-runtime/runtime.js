/* Autona Scoreboard Composer runtime — no build step required. */
(function () {
  "use strict";

  var project = null;
  var elements = new Map();
  var previousValues = new Map();

  function showError(error) {
    var target = document.getElementById("composer-error");
    target.style.display = "block";
    target.textContent = "スコアボードを読み込めません。\n" +
      (error && error.message ? error.message : String(error));
    document.body.style.opacity = "1";
    console.error(error);
  }

  function validateProject(value) {
    if (!value || value.schemaVersion !== 1 || !Array.isArray(value.layers)) {
      throw new Error("project.json の形式またはschemaVersionが不正です。");
    }
    if (!value.canvas || value.canvas.width !== 1920 || value.canvas.height !== 1080) {
      throw new Error("キャンバスは1920×1080である必要があります。");
    }
    var ids = new Set();
    var layerTypes = new Set(["image", "data-text", "data-image", "game-history"]);
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
      if (!layer.animation || !presets.has(layer.animation.preset) ||
          !Number.isFinite(layer.animation.durationMs) ||
          !Number.isFinite(layer.animation.delayMs)) {
        throw new Error(layer.name + " のanimationが不正です。");
      }
      if (layer.type === "image" &&
          (!/^assets\/[^/]+\.png$/i.test(layer.asset) || layer.asset.includes(".."))) {
        throw new Error(layer.name + " のassetが不正です。");
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
      if (field === "seed") return player.seed ? "SEED " + player.seed : "";
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

  function animationFrames(preset, opacity) {
    var end = {opacity: opacity, transform: "translate(0, 0) scale(1)"};
    if (preset === "fade") return [{opacity: 0}, end];
    if (preset === "slide-left") {
      return [{opacity: 0, transform: "translate(-80px, 0)"}, end];
    }
    if (preset === "slide-right") {
      return [{opacity: 0, transform: "translate(80px, 0)"}, end];
    }
    if (preset === "slide-up") {
      return [{opacity: 0, transform: "translate(0, -80px)"}, end];
    }
    if (preset === "slide-down") {
      return [{opacity: 0, transform: "translate(0, 80px)"}, end];
    }
    if (preset === "zoom") {
      return [{opacity: 0, transform: "scale(.72)"}, end];
    }
    return [end, end];
  }

  function playIntro(layer, content) {
    if (!layer.animation || layer.animation.preset === "none") return;
    content.animate(animationFrames(layer.animation.preset, layer.opacity), {
      duration: Math.max(0, Number(layer.animation.durationMs) || 0),
      delay: Math.max(0, Number(layer.animation.delayMs) || 0),
      easing: "cubic-bezier(.2,.8,.2,1)",
      fill: "both"
    });
  }

  function crossFade(content, opacity) {
    content.animate([{opacity: 0}, {opacity: opacity}], {
      duration: 180,
      easing: "ease-out"
    });
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
      transform: "rotate(" + layer.rotation + "deg)",
      zIndex: String(layer.zIndex),
      display: layer.visible ? "block" : "none"
    });

    var content = document.createElement("div");
    content.className = "composer-content";
    content.style.opacity = String(layer.opacity);
    element.appendChild(content);

    if (layer.type === "image") {
      var staticImage = document.createElement("img");
      staticImage.src = layer.asset;
      staticImage.alt = "";
      staticImage.style.objectFit = layer.objectFit || "contain";
      content.appendChild(staticImage);
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
    elements.set(layer.id, {element: element, content: content});
  }

  function fitText(container) {
    var content = container.querySelector(".composer-text-content");
    if (!content) return;
    content.style.transform = "";
    var scale = Math.min(1, container.clientWidth / Math.max(1, content.scrollWidth));
    content.style.transform = "scaleX(" + scale + ")";
  }

  function renderGames(layer, container, state) {
    var bestOf = Math.max(0, Number(resolveBinding("games", state)) || 0);
    var p1 = Math.max(0, Number(resolveBinding("p1.score", state)) || 0);
    var p2 = Math.max(0, Number(resolveBinding("p2.score", state)) || 0);
    var signature = [bestOf, p1, p2].join(":");
    if (previousValues.get(layer.id) === signature) return false;
    previousValues.set(layer.id, signature);
    container.replaceChildren();
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
    return true;
  }

  function updateLayer(layer, state, firstRun) {
    var refs = elements.get(layer.id);
    if (!refs || !layer.visible) return;
    var changed = false;

    if (layer.type === "data-text") {
      var value = String(resolveBinding(layer.binding, state));
      if (previousValues.get(layer.id) !== value) {
        refs.content.querySelector(".composer-text-content").textContent = value;
        previousValues.set(layer.id, value);
        fitText(refs.content.querySelector(".composer-text"));
        changed = true;
      }
    } else if (layer.type === "data-image") {
      var source = String(resolveBinding(layer.binding, state));
      if (previousValues.get(layer.id) !== source) {
        refs.content.querySelector("img").src = source;
        refs.content.style.visibility = source ? "visible" : "hidden";
        previousValues.set(layer.id, source);
        changed = true;
      }
    } else if (layer.type === "game-history") {
      changed = renderGames(
        layer,
        refs.content.querySelector(".composer-games"),
        state
      );
    }

    if (changed && !firstRun) crossFade(refs.content, layer.opacity);
  }

  function renderState(state, firstRun) {
    if (!project || !state || !state.score || !state.score["1"]) return;
    project.layers.forEach(function (layer) {
      updateLayer(layer, state, firstRun);
    });
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

  loadProject()
    .then(function (loaded) {
      project = loaded;
      buildProject();

      Start = async function () {
        document.body.style.opacity = "1";
        project.layers.forEach(function (layer) {
          var refs = elements.get(layer.id);
          if (refs && layer.visible) playIntro(layer, refs.content);
        });
      };

      Update = async function (event) {
        renderState(event.data, previousValues.size === 0);
      };

      return LoadEverything();
    })
    .catch(showError);
})();
