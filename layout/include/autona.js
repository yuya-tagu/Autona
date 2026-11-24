/* Autona overlay contract 1 (ADR-008 D1): what a layout reads, as a view.
 *
 * Load right after globals.js, before the layout's own scripts (it warns
 * once when LoadEverything() has run before it). The view and prev are
 * frozen: listeners share them.
 *
 *
 *   <script src="../include/globals.js"></script>
 *   <script src="../include/autona.js"></script>
 *   <script>
 *     Autona.onUpdate((view, prev) => {
 *       document.querySelector("#p1-name").textContent = view.board.p1.name;
 *     });
 *   </script>
 *
 * The transport is globals.js's, untouched (Socket.IO, deltas, the file
 * fallback, reconnects): this listens to its "tsh_update" event and turns
 * the state into the view. It never sets the global Start / Update, so a
 * layout that defines Update (as the shipped ones do) still gets onUpdate.
 *
 * The view's keys, types, values when the state has none and the state each
 * comes from are layout/include/overlay_contract.json (the table this is
 * held to: tests/test_overlay_contract.py). Contract 1 only ever gains keys;
 * a rename, a removal or a change of meaning is contract 2.
 *
 * Autona.raw is the state as it is: outside the contract, it may change
 * shape in any version.
 *
 * Served over http only (the app's preview, the OBS URL): image URLs are
 * absolute paths from the origin (/assets/..., /user_data/...).
 *
 * This file's URL parameters are prefixed "autona" and read with
 * URLSearchParams, never from window (globals.js copies every parameter
 * onto window, over any global of the same name):
 *   scoreboardNumber   the board (1 when absent): globals.js's own
 *   autonaSample=NAME  draw layout/include/sample/NAME.json instead of the
 *                      app's state
 *
 * In a frame (the app's preview: window.parent is another window) it also
 * tells the page around it what went wrong (ADR-008 D3), with postMessage:
 * an exception of an onUpdate listener (as often as it is logged), the
 * page's uncaught errors and unhandled rejections, and its own failures --
 * then "autona:ready" once the page has parsed. Every message has exactly
 * these keys, its strings cut to a length, at most POST_MAX errors a page:
 *   {type: "autona:ready" | "autona:error", contract, kind, message,
 *    source, line, column}
 * Only text about the layout's own errors: sent to any parent ("*"; the
 * page around it checks who sent it). OBS has no parent: nothing is sent.
 *
 * Template contract 2 (ADR-008 D7 R2): a thumbnail or result template draws
 * with Autona.onRender instead of defining initThumbnail / initResult and
 * saying RENDER_READY itself. It loads autona.js without globals.js:
 *
 *   <script src="../include/autona.js"></script>
 *   <script>
 *     Autona.onRender(async (payload) => {
 *       document.querySelector("#p1-name").textContent = ...;
 *     });
 *   </script>
 *
 * The listener gets the payload the app's service builds (the same object
 * contract 1's initThumbnail / initResult get: {data, config, template} /
 * {data, config, layout}) and may return a promise. This file then waits for
 * the fonts (document.fonts.ready) and the images (<img> and CSS
 * backgrounds; at most ASSET_WAIT_MS) and says the drawing is done. A
 * render with an error is no drawing: an exception of the listener (thrown
 * or a rejected promise), an uncaught error or unhandled rejection of the
 * page while it draws, or no listener at all. The errors of the page
 * before its first render (while it loaded) are the first render's; one
 * after a render ended (a rAF, a timer, a promise the listener did not
 * await) is that render's -- the app reads them again before it takes the
 * picture -- never the next one's. Such a render's picture is not taken;
 * its errors are the render's result (the app shows them as the generate's
 * error). A resource that failed to load is not an error (as for overlays).
 * One render at a time: one asked for while another draws waits for it,
 * and only the newest of those is drawn.
 *
 * Who asks for a render:
 *   - the app's off-screen renderer: Autona._render(payload, serial), and
 *     the page's title says "AUTONA_RENDER <serial> ok | failed" when it is
 *     done; Autona._renderResult(serial) is then its errors (so far: also
 *     while it draws), as JSON. Both are the renderer's, not the template's
 *     (not enumerable);
 *   - the app's preview, in a frame whose URL names the page around it
 *     (?autonaParent=<its origin>): it posts exactly
 *     {type: "autona:render", serial, payload} to the frame (only a message
 *     from window.parent, from that origin, of that shape, with a serial
 *     above the last one taken, is drawn), and this file posts the render's
 *     errors as above (kind "onRender", "error", ...), contract 2, then
 *     {type: "autona:rendered", contract: 2, serial, ok} once it is drawn.
 */
(function () {
  "use strict";

  var CONTRACT = 1;
  // The template contract onRender is (contract 1 is initThumbnail /
  // initResult): what this file's messages say on a page that renders.
  var RENDER_CONTRACT = 2;
  // How long a render waits for its fonts and images, at most.
  var ASSET_WAIT_MS = 5000;
  // How many errors a render keeps.
  var RENDER_ERRORS_MAX = 20;

  // onUpdate's listeners, and onRender's (one).
  var listeners = [];
  var renderListener = null;
  // A page that renders: it called onRender and not onUpdate.
  function renders() {
    return renderListener !== null && listeners.length === 0;
  }

  // ── telling the app's preview (ADR-008 D3) ─────────────────────────────

  var MESSAGE_MAX = 500;
  var SOURCE_MAX = 300;
  var POST_MAX = 50;
  var framed = (function () {
    try {
      return Boolean(window.parent) && window.parent !== window;
    } catch (error) {
      return false;
    }
  })();
  var postedErrors = 0;
  // The page around the frame that may ask for renders (the app's preview
  // names its origin in the frame's URL); "" for none.
  var parentOrigin = (function () {
    try {
      var named = new URLSearchParams(window.location.search)
        .get("autonaParent") || "";
      return /^https?:\/\/[^\/?#]+$/.test(named) ? named : "";
    } catch (error) {
      return "";
    }
  })();

  function clip(value, max) {
    var textValue = typeof value === "string" ? value : String(value);
    return textValue.length > max ? textValue.slice(0, max - 1) + "…"
      : textValue;
  }

  function lineNumber(value) {
    return typeof value === "number" && isFinite(value) && value > 0
      ? Math.floor(value) : null;
  }

  // A script's URL as a path when it is this page's origin's (shorter).
  function sourcePath(url) {
    if (typeof url !== "string" || !url) return "";
    var origin = window.location.protocol + "//" + window.location.host;
    return clip(url.indexOf(origin + "/") === 0 ? url.slice(origin.length)
      : url, SOURCE_MAX);
  }

  // Where an Error was thrown: the first "url:line:column" of its stack.
  function thrownAt(error) {
    var stack = error && typeof error.stack === "string" ? error.stack : "";
    var match = /(https?:\/\/[^\s()]+?):(\d+):(\d+)/.exec(stack);
    return match
      ? {source: match[1], line: Number(match[2]), column: Number(match[3])}
      : {source: "", line: null, column: null};
  }

  function errorText(error) {
    if (error && typeof error === "object" && typeof error.message === "string") {
      return (error.name ? error.name + ": " : "") + error.message;
    }
    return String(error);
  }

  function post(type, kind, message, source, line, column) {
    if (!framed) return;
    if (type === "autona:error") {
      if (postedErrors >= POST_MAX) return;
      postedErrors += 1;
    }
    try {
      window.parent.postMessage({
        type: type,
        contract: renders() ? RENDER_CONTRACT : CONTRACT,
        kind: kind,
        message: clip(message, MESSAGE_MAX),
        source: sourcePath(source),
        line: lineNumber(line),
        column: lineNumber(column),
      }, parentOrigin || "*");
    } catch (error) {
      // A parent that cannot be told: nothing to do.
    }
  }

  // Whose an error is: the render running; else the last one that ran
  // (an error after it ended is its own, not the next one's); else -- no
  // render yet, the page loading -- the first render's (caught).
  var caught = [];
  var rendering = null;
  var lastRecord = null;

  function keep(record, kind, message, source, line, column) {
    var list = record ? record.errors : caught;
    if (list.length >= RENDER_ERRORS_MAX) return;
    list.push({
      kind: kind,
      message: clip(message, MESSAGE_MAX),
      source: sourcePath(source),
      line: lineNumber(line),
      column: lineNumber(column),
    });
  }

  // An error of the page's: kept for a render, and told to the preview.
  function report(kind, message, source, line, column) {
    keep(rendering || lastRecord, kind, message, source, line, column);
    post("autona:error", kind, message, source, line, column);
  }

  // kind: "onUpdate" (a listener threw), "error" / "unhandledrejection" (the
  // page's), "autona" (this file's own trouble).
  function postError(kind, error) {
    var at = thrownAt(error);
    report(kind, errorText(error), at.source, at.line, at.column);
  }

  // Watched in a frame (the preview is told) and on a page that renders (the
  // render is told), which is not framed in the off-screen renderer.
  if (typeof window.addEventListener === "function") {
    window.addEventListener("error", function (event) {
      // A script's error (an ErrorEvent), not a resource that failed to load.
      if (!event || typeof event.message !== "string") return;
      var at = thrownAt(event.error);
      report("error", event.message || errorText(event.error),
             event.filename || at.source, event.lineno || at.line,
             event.colno || at.column);
    });
    window.addEventListener("unhandledrejection", function (event) {
      postError("unhandledrejection", event ? event.reason : event);
    });
  }
  var SAMPLE_TIMESTAMP = 1;
  var ICON_PACK = "base_files/icon";
  var IMAGE_PACK = "full";
  // The kind in the layout's settings.json -> its colors.{feature} override.
  var COLOR_FEATURES = {scoreboard: "board", commentary: "commentary",
                        top8: "top8"};
  // A folder without a kind: core/LayoutCatalog's folder-name convention
  // (_LEGACY_PREFIXES): [prefix, kind, needs "_" after the prefix].
  var FOLDER_KINDS = [["scoreboard", "scoreboard", false],
                      ["commentators", "commentary", true],
                      ["bracket_top8", "top8", true]];
  // core/Top8Model.MATCH_IDS, in its order.
  var TOP8_MATCH_IDS = ["wsf1", "wsf2", "lr1a", "lr1b", "wf", "lqf1", "lqf2",
                        "lsf", "lf", "gf", "gf_reset"];
  // view.options key -> display_options key, with the default
  // core/StateSchema.DISPLAY_OPTIONS gives (the value when the state has
  // none).
  var OPTIONS = [
    ["showSponsor", "show_sponsor", true],
    ["showName", "show_name", true],
    ["showSeed", "show_seed", true],
    ["showCharacters", "show_characters", true],
    ["showSocial", "show_social", true],
    ["showRealName", "show_real_name", false],
    ["showLocation", "show_location", true],
    ["showAdditional", "show_additional", false],
  ];

  // globals.js (the overlays' transport): a page that renders goes without
  // it. Whether the page is one is known once its scripts have run
  // (startLoading); an overlay without globals.js is told so then.
  var hasGlobals = typeof LoadEverything === "function";
  var noGlobals = "autona.js: load ../include/globals.js before autona.js "
    + "(an overlay), or call Autona.onRender (a thumbnail or result "
    + "template)";
  // LoadEverything() already called (the layout's script ran before this
  // one): its scripts are on the page, or its socket is open. Calling it
  // again would load everything twice, so this file does not.
  function loadingStarted() {
    if (!hasGlobals) return false;
    if (window.tshSocket) return true;
    if (typeof document.getElementsByTagName !== "function") return false;
    var scripts = document.getElementsByTagName("script");
    for (var i = 0; i < scripts.length; i += 1) {
      if (/\/include\/jquery-3\.7\.1\.min\.js$/.test(scripts[i].src || "")) {
        return true;
      }
    }
    return false;
  }
  var startedEarly = loadingStarted();
  if (startedEarly) {
    var lateLoad = "autona.js: load it right after globals.js, before the "
      + "layout's own scripts (LoadEverything() had already run)";
    console.warn(lateLoad);
    postError("autona", lateLoad);
  }
  if (window.location.protocol === "file:") {
    console.warn("autona.js: open this layout over http (the app's preview "
      + "or its OBS URL); its images do not load from file://");
  }

  var params = new URLSearchParams(window.location.search);

  // ── reading the state ──────────────────────────────────────────────────

  function obj(value) {
    return value && typeof value === "object" && !Array.isArray(value)
      ? value : {};
  }

  function text(value) {
    if (typeof value === "string") return value;
    if (typeof value === "number" && isFinite(value)) return String(value);
    return "";
  }

  function num(value) {
    if (typeof value === "number" && isFinite(value)) return value;
    if (typeof value === "string" && value.trim() !== "" &&
        isFinite(Number(value))) {
      return Number(value);
    }
    return null;
  }

  function positive(value) {
    var n = num(value);
    return n !== null && n > 0 ? n : null;
  }

  function handle(value) {
    return text(value).trim().replace(/^@+/, "");
  }

  // The values under "1", "2", ... in number order (the state's lists).
  function numbered(value) {
    var source = obj(value);
    return Object.keys(source)
      .filter(function (key) { return /^[1-9][0-9]*$/.test(key); })
      .sort(function (a, b) { return Number(a) - Number(b); })
      .map(function (key) { return {index: Number(key), value: source[key]}; });
  }

  // A file path from the state as a URL from this origin; "" when it is not
  // one this server serves (an absolute path, a file: URL, "..").
  function assetUrl(path) {
    if (typeof path !== "string") return "";
    var value = path.trim();
    if (!value) return "";
    if (/^(https?:|data:|blob:)/i.test(value)) return value;
    if (/^(file:|[a-z]:[\\/]|[\\/]{2})/i.test(value)) return "";
    var parts = value.replace(/\\/g, "/").split("/")
      .filter(function (part) { return part && part !== "."; });
    if (!parts.length || parts.indexOf("..") !== -1) return "";
    return "/" + parts.map(encodeURIComponent).join("/");
  }

  function place(value) {
    var source = obj(value);
    return {
      code: text(source.code),
      name: text(source.display_name) || text(source.name),
      flag: assetUrl(source.asset),
    };
  }

  function emptyCharacter() {
    return {name: "", icon: "", image: "", images: []};
  }

  function character(value) {
    var source = obj(value);
    var images = [];
    var assets = obj(source.assets);
    // In pack-name order: the state's key order is the game's, not ours.
    Object.keys(assets).sort().forEach(function (pack) {
      var url = assetUrl(obj(assets[pack]).asset);
      if (url) images.push({pack: pack, url: url});
    });
    function find(pack) {
      var found = images.filter(function (image) { return image.pack === pack; });
      return found.length ? found[0].url : "";
    }
    var icon = find(ICON_PACK);
    var others = images.filter(function (image) { return image.pack !== ICON_PACK; });
    // The layout's own pick (settings.json's assets.default.asset_key, the
    // key the shipped layouts name), then full, then the first by name.
    var preferred = text(obj(obj(layoutSettings().assets).default).asset_key);
    return {
      name: text(source.name),
      icon: icon,
      image: (preferred && find(preferred)) || find(IMAGE_PACK) ||
        (others.length ? others[0].url : icon),
      images: images,
    };
  }

  function characters(value) {
    return numbered(value)
      .filter(function (entry) { return text(obj(entry.value).name).trim(); })
      .map(function (entry) { return character(entry.value); });
  }

  function player(value, crew, index, activeMember) {
    var source = obj(value);
    var picked = characters(source.character);
    return {
      name: text(source.name),
      sponsor: text(source.team),
      sponsorLogo: assetUrl(source.sponsor_logo),
      realName: text(source.real_name),
      twitter: handle(source.twitter),
      seed: positive(source.seed),
      country: place(source.country),
      region: place(source.state),
      character: picked.length ? picked[0] : emptyCharacter(),
      characters: picked,
      stock: crew ? num(source.stock) : null,
      active: crew && activeMember !== "" && String(index) === activeMember,
    };
  }

  function side(value, crew) {
    var team = obj(value);
    var members = numbered(team.player);
    var activeMember = "";
    if (crew) {
      var memberNum = positive(team.memberNum);
      if (memberNum !== null) {
        members = members.filter(function (entry) {
          return entry.index <= memberNum;
        });
      }
      activeMember = text(team.activeMember);
      if (activeMember === "0") activeMember = "";
    }
    var players = members.map(function (entry) {
      return player(entry.value, crew, entry.index, activeMember);
    });
    var first = players.length ? players[0] : player({}, false, 0, "");
    var view = {};
    Object.keys(first).forEach(function (key) {
      if (key !== "stock" && key !== "active") view[key] = first[key];
    });
    view.score = num(team.score);
    view.losers = team.losers === true;
    view.teamName = text(team.teamName);
    view.color = text(team.color);
    view.players = players;
    return view;
  }

  function board(state, number) {
    var source = obj(obj(state.score)[String(number)]);
    var teams = obj(source.team);
    // Only board 3 is a crew board (core/StateSchema: its keys exist there).
    var crew = number === 3;
    return {
      number: number,
      // 0 is "not set" (the shipped scoreboards draw no game boxes then).
      bestOf: positive(source.best_of),
      phase: text(source.phase),
      pool: text(source.pool),
      round: text(source.match),
      station: text(source.station),
      p1: side(teams["1"], crew),
      p2: side(teams["2"], crew),
    };
  }

  function layoutSettings() {
    return obj(typeof tsh_settings === "object" ? tsh_settings : null);
  }

  // The layout's kind: its settings.json's autona.kind, else (no block, or
  // a kind the catalog does not take, which it also ignores) its folder's
  // name by the catalog's convention.
  function layoutKind() {
    var kind = text(obj(layoutSettings().autona).kind);
    if (COLOR_FEATURES[kind]) return kind;
    var matched = /\/layout\/([^/]+)\/[^/]*$/.exec(window.location.pathname || "");
    if (!matched) return "";
    var folder;
    try {
      folder = decodeURIComponent(matched[1]);
    } catch (error) {
      folder = matched[1];
    }
    for (var i = 0; i < FOLDER_KINDS.length; i += 1) {
      var prefix = FOLDER_KINDS[i][0];
      if (FOLDER_KINDS[i][2]
        ? folder === prefix || folder.indexOf(prefix + "_") === 0
        : folder.indexOf(prefix) === 0) {
        return FOLDER_KINDS[i][1];
      }
    }
    return "";
  }

  function tournament(state) {
    var info = obj(state.tournamentInfo);
    var feature = COLOR_FEATURES[layoutKind()];
    var colors = feature ? obj(obj(state.colors)[feature]) : {};
    var logoVersion = positive(info.logoVersion);
    return {
      name: text(info.displayTournamentName) || text(info.tournamentName),
      eventName: text(info.eventName),
      logo: logoVersion === null ? ""
        : "/layout/logo.png?v=" + encodeURIComponent(String(logoVersion)),
      themeColor: text(colors.themeColor) || text(info.themeColor),
      accentColor: text(colors.accentColor) || text(info.accentColor),
    };
  }

  function commentary(state) {
    return numbered(state.commentary)
      .map(function (entry) {
        var caster = obj(entry.value);
        // display: 0 hidden, 1.. its place (a legacy true is 1).
        var order = caster.display === true ? 1 : num(caster.display) || 0;
        return {order: order, slot: entry.index, caster: caster};
      })
      .filter(function (entry) {
        return entry.order > 0 && text(entry.caster.name).trim();
      })
      .sort(function (a, b) { return a.order - b.order || a.slot - b.slot; })
      .map(function (entry) {
        var caster = entry.caster;
        return {
          name: text(caster.name),
          sponsor: text(caster.team),
          twitter: handle(caster.twitter),
          role: text(caster.role),
          iconName: text(caster.icon),
          note: text(caster.note),
        };
      });
  }

  function top8Player(value) {
    var source = obj(value);
    var picked = characters(source.characters);
    return {
      name: text(source.name),
      sponsor: text(source.sponsor),
      country: place(source.country),
      character: picked.length ? picked[0] : emptyCharacter(),
      characters: picked,
    };
  }

  function top8(state) {
    var source = obj(state.top8);
    var players = obj(source.players);
    var matchesIn = obj(source.matches);
    var matches = {};
    TOP8_MATCH_IDS.forEach(function (id) {
      var match = obj(matchesIn[id]);
      var slots = Array.isArray(match.slots) ? match.slots : [];
      var score = Array.isArray(match.score) ? match.score : [];
      var finished = match.finished === true;
      var scores = [num(score[0]), num(score[1])];
      var winner = null;
      if (finished && scores[0] !== null && scores[1] !== null &&
          scores[0] !== scores[1]) {
        winner = scores[0] > scores[1] ? 1 : 2;
      }
      var view = {name: text(match.name)};
      [0, 1].forEach(function (i) {
        // A slot naming no player (none of the eight) is undecided.
        var slot = positive(slots[i]);
        var known = slot !== null && players[String(slot)] &&
          typeof players[String(slot)] === "object";
        var entry = top8Player(known ? players[String(slot)] : {});
        entry.slot = known ? slot : null;
        entry.score = scores[i];
        view["p" + (i + 1)] = entry;
      });
      view.finished = finished;
      view.active = match.active === true;
      view.winner = winner;
      matches[id] = view;
    });
    return {
      phaseLabel: text(source.phaseLabel),
      players: numbered(source.players).map(function (entry) {
        return top8Player(entry.value);
      }),
      matches: matches,
    };
  }

  function options(state) {
    var source = obj(state.display_options);
    var view = {};
    OPTIONS.forEach(function (option) {
      var value = source[option[1]];
      view[option[0]] = typeof value === "boolean" ? value : option[2];
    });
    return view;
  }

  function boardNumber() {
    var value = params.get("scoreboardNumber");
    var n = Number(value);
    return value !== null && /^[1-9][0-9]*$/.test(value.trim()) ? n : 1;
  }

  function buildView(state) {
    var source = obj(state);
    return {
      board: board(source, boardNumber()),
      tournament: tournament(source),
      commentary: commentary(source),
      top8: top8(source),
      options: options(source),
    };
  }

  // ── handing it to the layout ──────────────────────────────────────────

  // listeners: {fn, failing}: failing while its last call threw, so a
  // listener that throws on every update is reported once, and again only
  // after a call that succeeded.
  var current = null;
  var currentKey = "";
  var rawState = null;

  // The view is shared by every listener (and is the next one's prev):
  // frozen, so one cannot change what another reads.
  function deepFreeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.keys(value).forEach(function (key) { deepFreeze(value[key]); });
      Object.freeze(value);
    }
    return value;
  }

  function failed(record, error) {
    if (!record.failing) {
      console.error("Autona.onUpdate:", error);
      postError("onUpdate", error);
    }
    record.failing = true;
  }

  function call(record, view, prev) {
    try {
      var result = record.fn(view, prev);
      if (result && typeof result.then === "function") {
        result.then(function () { record.failing = false; },
                    function (error) { failed(record, error); });
      } else {
        record.failing = false;
      }
    } catch (error) {
      failed(record, error);
    }
  }

  function onStateUpdate(event) {
    rawState = event.data;
    var view;
    try {
      view = buildView(event.data);
    } catch (error) {
      console.error("autona.js: could not read the state:", error);
      postError("autona", error);
      return;
    }
    // Called only when the view changed: a state update this contract does
    // not show (statistics, the set list ...) calls nothing.
    var key = JSON.stringify(view);
    if (current !== null && key === currentKey) return;
    var prev = current;
    current = deepFreeze(view);
    currentKey = key;
    listeners.slice().forEach(function (record) {
      call(record, view, prev);
    });
  }

  document.addEventListener("tsh_update", onStateUpdate);

  // globals.js has the layout call LoadEverything(); a layout written for
  // this file need not, so it is called once the page has parsed, and any
  // call after the first (the layout's own, ours) gets the first's promise.
  if (hasGlobals) {
    var loadEverything = LoadEverything;
    // Already running (see startedEarly): only the layout's call loads.
    var loading = startedEarly ? Promise.resolve() : null;
    LoadEverything = function () {
      if (loading === null) loading = loadEverything.apply(this, arguments);
      return loading;
    };
  }
  function startLoading() {
    setTimeout(function () {
      // The page has parsed and its scripts have run (their errors are
      // told already). An overlay without globals.js has nothing to start.
      if (!hasGlobals && !renders()) {
        console.error(noGlobals);
        postError("autona", noGlobals);
        return;
      }
      // The preview hears that autona.js is there.
      post("autona:ready", "ready", "", window.location.pathname, null, null);
      // A page that renders reads no state: it is given its payload.
      if (!hasGlobals || renders()) return;
      LoadEverything().then(null, function (error) {
        console.error("autona.js: the page could not start:", error);
        postError("autona", error);
      });
    }, 0);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startLoading);
  } else {
    startLoading();
  }

  // ── the sample state (?autonaSample=NAME) ─────────────────────────────

  var sampleName = params.get("autonaSample");
  if (sampleName !== null && hasGlobals) {
    var sample = /^[A-Za-z0-9_-]{1,64}$/.test(sampleName)
      ? fetch("/layout/include/sample/" + sampleName + ".json",
              {cache: "no-store"})
        .then(function (response) {
          if (!response.ok) {
            throw new Error("no sample named " + sampleName);
          }
          return response.json();
        })
      : Promise.reject(new Error("not a sample name: " + sampleName));
    sample.then(null, function (error) {
      console.error("autona.js: autonaSample:", error);
      postError("autona", error);
    });
    // globals.js reads the state through getData() (its file transport, and
    // again when OBS shows the source): give it the sample, at one fixed
    // timestamp so a second read changes nothing. A fresh copy every time:
    // globals.js applies changes to the object it holds.
    getData = function () {
      return sample.then(function (state) {
        var copy = JSON.parse(JSON.stringify(obj(state)));
        copy.timestamp = SAMPLE_TIMESTAMP;
        return copy;
      });
    };
    // No Socket.IO: the sample is read once the page is ready, as the file
    // transport would read program_state.json.
    UpdateData_SocketIO = async function () {
      document.addEventListener("tsh_init", function () {
        setTimeout(UpdateData, 0);
      }, {once: true});
    };
  }

  // ── rendering (template contract 2) ───────────────────────────────────

  function nothing() {}

  // Settled once `image` has loaded (or failed) and is decoded.
  function imageSettled(image) {
    if (!image || !(image.currentSrc || image.src)) return null;
    var loaded = image.complete ? Promise.resolve()
      : new Promise(function (resolve) {
        image.addEventListener("load", resolve, {once: true});
        image.addEventListener("error", resolve, {once: true});
      });
    return loaded.then(function () {
      return typeof image.decode === "function" && image.naturalWidth
        ? image.decode() : null;
    });
  }

  function urlSettled(url) {
    if (typeof Image !== "function") return null;
    var image = new Image();
    image.src = url;
    return imageSettled(image);
  }

  // The URLs of a computed background-image ("url(...)", maybe several).
  function backgroundUrls(value) {
    var urls = [];
    var pattern = /url\(\s*(["']?)(.*?)\1\s*\)/g;
    var match;
    while (typeof value === "string" && (match = pattern.exec(value))) {
      if (match[2]) urls.push(match[2]);
    }
    return urls;
  }

  // Settled once the fonts are in and the images decoded, or ASSET_WAIT_MS
  // later: what the drawing shows is there when the render says done. An
  // image that does not load is waited for no longer (not an error).
  function assetsSettled() {
    var waits = [];
    function wait(promise) {
      if (promise) waits.push(Promise.resolve(promise).then(nothing, nothing));
    }
    try {
      if (document.fonts && document.fonts.ready) wait(document.fonts.ready);
      var images = document.images || [];
      for (var i = 0; i < images.length; i += 1) wait(imageSettled(images[i]));
      if (typeof document.querySelectorAll === "function"
          && typeof getComputedStyle === "function") {
        var seen = {};
        var elements = document.querySelectorAll("*");
        for (var j = 0; j < elements.length; j += 1) {
          backgroundUrls(getComputedStyle(elements[j]).backgroundImage)
            .forEach(function (url) {
              if (seen[url]) return;
              seen[url] = true;
              wait(urlSettled(url));
            });
        }
      }
    } catch (error) {
      // What cannot be looked at is not waited for.
    }
    var timer = null;
    return Promise.race([
      Promise.all(waits),
      new Promise(function (resolve) {
        timer = setTimeout(resolve, ASSET_WAIT_MS);
      }),
    ]).then(function () { clearTimeout(timer); });
  }

  // A listener's exception: kept for its render, told to the preview while
  // that render is the one running (a render overtaken says nothing more).
  function renderFailed(record, error) {
    var at = thrownAt(error);
    keep(record, "onRender", errorText(error), at.source, at.line, at.column);
    if (rendering === record) {
      post("autona:error", "onRender", errorText(error), at.source, at.line,
           at.column);
    }
  }

  // The latest renders' records, newest last: _renderResult reads them.
  var RECORDS_KEPT = 4;
  var records = [];

  function outcomeOf(record) {
    return {serial: record.serial, ok: record.errors.length === 0,
            done: record.done, errors: record.errors.slice()};
  }

  // Draw `payload`: settles to {serial, ok, done, errors}. The first render
  // takes the errors of the page's loading too.
  function render(payload, serial) {
    var record = {serial: serial, done: false,
                  errors: caught.splice(0, caught.length)};
    records.push(record);
    if (records.length > RECORDS_KEPT) records.shift();
    rendering = record;
    // The preview shows each render's errors afresh: the cap is a render's.
    postedErrors = 0;
    var drawn;
    if (renderListener === null) {
      var none = "autona.js: the page did not call Autona.onRender";
      keep(record, "autona", none, window.location.pathname, null, null);
      post("autona:error", "autona", none, window.location.pathname, null,
           null);
      drawn = Promise.resolve();
    } else {
      var listener = renderListener;
      drawn = new Promise(function (resolve) {
        var result;
        try {
          result = listener(payload);
        } catch (error) {
          renderFailed(record, error);
          resolve();
          return;
        }
        Promise.resolve(result).then(function () { resolve(); },
                                     function (error) {
                                       renderFailed(record, error);
                                       resolve();
                                     });
      });
    }
    return drawn.then(assetsSettled).then(function () {
      record.done = true;
      if (rendering === record) rendering = null;
      lastRecord = record;
      return outcomeOf(record);
    });
  }

  // One render at a time (a render overtaken would draw over a newer one):
  // one asked for while another draws waits, and a newer one asked for
  // meanwhile takes its place (and is not drawn, nor says anything).
  // `then(outcome)` follows its render.
  var drawing = false;
  var next = null;

  function queue(payload, serial, then) {
    if (drawing) {
      next = {payload: payload, serial: serial, then: then};
      return;
    }
    drawing = true;
    render(payload, serial).then(function (outcome) {
      drawing = false;
      try {
        then(outcome);
      } finally {
        var waiting = next;
        next = null;
        if (waiting !== null) {
          queue(waiting.payload, waiting.serial, waiting.then);
        }
      }
    });
  }

  // The preview's render: {type: "autona:render", serial, payload} from the
  // page around the frame, and nothing else.
  var framedSerial = 0;
  function renderMessage(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)
        || Object.getPrototypeOf(data) !== Object.prototype) {
      return null;
    }
    var keys = Object.keys(data);
    if (keys.length !== 3 || data.type !== "autona:render"
        || typeof data.serial !== "number" || !isFinite(data.serial)
        || Math.floor(data.serial) !== data.serial || data.serial < 1
        || !data.payload || typeof data.payload !== "object"
        || Array.isArray(data.payload)) {
      return null;
    }
    return data;
  }
  function rendered(outcome) {
    try {
      window.parent.postMessage({
        type: "autona:rendered", contract: RENDER_CONTRACT,
        serial: outcome.serial, ok: outcome.ok}, parentOrigin);
    } catch (error) {
      // A parent that cannot be told: nothing to do.
    }
  }
  if (framed && parentOrigin && typeof window.addEventListener === "function") {
    window.addEventListener("message", function (event) {
      if (!event || event.source !== window.parent
          || event.origin !== parentOrigin) {
        return;
      }
      var message = renderMessage(event.data);
      if (message === null || message.serial <= framedSerial) return;
      framedSerial = message.serial;
      queue(message.payload, message.serial, rendered);
    });
  }

  window.Autona = {
    contract: CONTRACT,
    onUpdate: function (listener) {
      if (typeof listener !== "function") {
        throw new TypeError("Autona.onUpdate takes a function");
      }
      var record = {fn: listener, failing: false};
      listeners.push(record);
      // Registered after the first update: hand it the view now (unless a
      // newer one has reached it meanwhile).
      if (current !== null) {
        var view = current;
        setTimeout(function () {
          if (current === view && listeners.indexOf(record) !== -1) {
            call(record, view, null);
          }
        }, 0);
      }
      return function () {
        var index = listeners.indexOf(record);
        if (index !== -1) listeners.splice(index, 1);
      };
    },
    // Template contract 2: `listener(payload)` draws a render (see the top).
    onRender: function (listener) {
      if (typeof listener !== "function") {
        throw new TypeError("Autona.onRender takes a function");
      }
      if (renderListener !== null) {
        throw new Error("Autona.onRender takes one listener: it was called "
          + "before");
      }
      renderListener = listener;
      return function () {
        if (renderListener === listener) renderListener = null;
      };
    },
  };
  Object.defineProperty(window.Autona, "raw", {
    enumerable: true,
    get: function () { return rawState; },
  });
  // The off-screen renderer's (AutonaOffscreenRenderer), not a template's.
  Object.defineProperty(window.Autona, "_render", {
    value: function (payload, serial) {
      queue(payload, serial, function (outcome) {
        document.title = "AUTONA_RENDER " + serial
          + (outcome.ok ? " ok" : " failed");
      });
    },
  });
  // A render's errors so far (also while it draws): read again before the
  // picture is taken, and at a timeout.
  Object.defineProperty(window.Autona, "_renderResult", {
    value: function (serial) {
      for (var i = records.length - 1; i >= 0; i -= 1) {
        if (records[i].serial === serial) {
          return JSON.stringify(outcomeOf(records[i]));
        }
      }
      return null;
    },
  });
})();
