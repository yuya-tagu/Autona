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
 */
(function () {
  "use strict";

  var CONTRACT = 1;
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

  if (typeof LoadEverything !== "function") {
    console.error("autona.js: load ../include/globals.js before autona.js");
    return;
  }
  // LoadEverything() already called (the layout's script ran before this
  // one): its scripts are on the page, or its socket is open. Calling it
  // again would load everything twice, so this file does not.
  function loadingStarted() {
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
    console.warn("autona.js: load it right after globals.js, before the "
      + "layout's own scripts (LoadEverything() had already run)");
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

  // {fn, failing}: failing while its last call threw, so a listener that
  // throws on every update is reported once, and again only after a call
  // that succeeded.
  var listeners = [];
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
    if (!record.failing) console.error("Autona.onUpdate:", error);
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
  var loadEverything = LoadEverything;
  // Already running (see startedEarly): only the layout's call loads.
  var loading = startedEarly ? Promise.resolve() : null;
  LoadEverything = function () {
    if (loading === null) loading = loadEverything.apply(this, arguments);
    return loading;
  };
  function startLoading() {
    setTimeout(function () {
      LoadEverything().then(null, function (error) {
        console.error("autona.js: the page could not start:", error);
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
  if (sampleName !== null) {
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
  };
  Object.defineProperty(window.Autona, "raw", {
    enumerable: true,
    get: function () { return rawState; },
  });
})();
