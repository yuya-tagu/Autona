(function () {
  "use strict";

  // A path of the payload (data / config) as a URL this page can load. Those
  // paths are relative to Autona's folder ("./assets/...", "user_data/..."),
  // two folders up from this page; "/..." (Autona's own URL, such as
  // /render-asset/...), data: and blob: are used as they are. Anything else
  // (a drive path such as C:\..., file://, http(s)://) cannot be loaded
  // here: "". A path of template.json or of this folder is not one of
  // these: it is relative to this page already ("./bg.png").
  function resolve(path) {
    if (typeof path !== "string" || path === "") return "";
    if (/^(data|blob):/i.test(path)) return path;
    if (path.charAt(0) === "/" && path.charAt(1) !== "/") return path;
    if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.charAt(0) === "/"
        || path.charAt(0) === "\\") {
      return "";
    }
    return "../../" + path.replace(/^\.\//, "");
  }

  // Show `url` in the <img id>, or hide it ("" or an image that failed to
  // load: a 404 is no error of the render, nothing to show either).
  function setImage(id, url) {
    var image = document.getElementById(id);
    image.onerror = function () { image.hidden = true; };
    if (url) {
      if (image.getAttribute("src") !== url) image.src = url;
      image.hidden = false;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
    }
    return image;
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text || "";
  }

  // The players of a team, in their order ("1", "2", ...).
  function playersOf(team) {
    var players = (team && team.player) || {};
    return Object.keys(players).sort(function (a, b) { return a - b; })
      .map(function (key) { return players[key] || {}; });
  }

  // A player's first character's picture ({asset, eyesight}): the full
  // body, else the first picture the game's assets have. null when none.
  function characterAsset(player) {
    var characters = (player && player.character) || {};
    var first = characters["1"] || {};
    var assets = first.assets || {};
    var asset = assets.full || assets[Object.keys(assets)[0]];
    return asset && asset.asset ? asset : null;
  }

  // The character's picture in its frame, as the thumbnail page's
  // "character image placement" sets it (config.game[codename]): mirrored
  // (flip_p1 / flip_p2), filling the frame times `zoom` %, its eyes
  // (eyesight, else the picture's middle) at `align` % of the frame without
  // leaving a gap at an edge, pixelated unless `smooth_scale`.
  async function placeCharacter(number, asset, game) {
    var frame = document.getElementById("p" + number + "-frame");
    frame.classList.toggle("flipped", !!game["flip_p" + number]);
    var image = setImage("p" + number + "-character", resolve(asset && asset.asset));
    if (image.hidden) return;
    image.style.imageRendering = game.smooth_scale === false ? "pixelated" : "auto";
    try {
      await image.decode();
    } catch (error) {
      image.hidden = true;  // Not loaded: nothing to place.
      return;
    }
    var width = image.naturalWidth;
    var height = image.naturalHeight;
    var frameWidth = frame.clientWidth;
    var frameHeight = frame.clientHeight;
    var scale = Math.max(frameWidth / width, frameHeight / height)
      * Math.max(1, (game.zoom || 100) / 100);
    var eye = asset.eyesight || {x: width / 2, y: height / 2};
    var align = game.align || {};
    var left = frameWidth * (align.horizontal || 50) / 100 - eye.x * scale;
    var top = frameHeight * (align.vertical || 40) / 100 - eye.y * scale;
    image.style.width = width * scale + "px";
    image.style.height = height * scale + "px";
    image.style.left = Math.min(0, Math.max(frameWidth - width * scale, left)) + "px";
    image.style.top = Math.min(0, Math.max(frameHeight - height * scale, top)) + "px";
  }

  // One side: the team's name (when the page says so and there is one),
  // else its players' names; the sponsor of a lone player; the first
  // player's character (a sample: a design may show every player's).
  function drawSide(number, team, config, data, game) {
    var players = playersOf(team);
    var teamName = config.use_team_names && team ? team.teamName : "";
    var showSponsor = config.use_sponsors !== false
      && !(data.display_options && data.display_options.show_sponsor === false);

    setText("p" + number + "-name", teamName || players.map(function (player) {
      return player.name || "";
    }).filter(Boolean).join(" / "));
    setText("p" + number + "-sponsor",
            !teamName && showSponsor && players.length === 1 ? players[0].team : "");
    return placeCharacter(number, characterAsset(players[0]), game);
  }

  Autona.onRender(async function (payload) {
    var data = payload.data || {};
    var config = payload.config || {};
    var template = payload.template || {};
    var match = (data.score && data.score[String(config.scoreboardNumber || 1)]) || {};
    var teams = match.team || {};
    var tournament = data.tournamentInfo || {};
    var games = config.game || {};
    var game = games[(data.game && data.game.codename) || ""] || {};

    // The operator's colors on the main parts; the others are the design's.
    var root = document.documentElement;
    root.style.setProperty("--theme", config.theme_color || "#181b21");
    root.style.setProperty("--accent", config.accent_color || "#f0a500");

    // The operator's background, else the template's own (template.json,
    // relative to this page), else none (the theme color shows).
    setImage("background", resolve(config.background_path)
      || template.default_background || "");

    // "TOP 8 - A1 - Winners Final": the phase, the pool, the round.
    setText("round", config.display_phase === false ? ""
      : [match.phase, match.pool, match.match].filter(Boolean).join(" - "));
    setText("tournament",
            tournament.displayTournamentName || tournament.tournamentName);

    // The characters are placed once their pictures are loaded.
    await Promise.all([drawSide(1, teams["1"], config, data, game),
                       drawSide(2, teams["2"], config, data, game)]);
  });
})();
