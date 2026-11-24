(function () {
  "use strict";

  // A path of the payload (data / config) as a URL this page can load. Those
  // paths are relative to Autona's folder ("./assets/...", "user_data/..."),
  // two folders up from this page; "/..." (Autona's own URL, such as
  // /render-asset/...), data: and blob: are used as they are. Anything else
  // (a drive path such as C:\..., file://, http(s)://) cannot be loaded
  // here: "". A path of this folder is not one of these: it is relative to
  // this page already ("./bg.png").
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

  // An <img> showing `url`, hidden when there is none or it fails to load (a
  // 404 is no error of the render, nothing to show either).
  function image(className, url) {
    var element = document.createElement("img");
    element.className = className;
    element.alt = "";
    element.onerror = function () { element.hidden = true; };
    if (url) element.src = url; else element.hidden = true;
    return element;
  }

  function setImage(id, url) {
    var element = document.getElementById(id);
    element.onerror = function () { element.hidden = true; };
    if (url) {
      if (element.getAttribute("src") !== url) element.src = url;
      element.hidden = false;
    } else {
      element.removeAttribute("src");
      element.hidden = true;
    }
  }

  // A <div> with `text` as text (never as markup: it is the operator's).
  function div(className, text) {
    var element = document.createElement("div");
    element.className = className;
    element.textContent = text || "";
    return element;
  }

  // "1st", "2nd", "3rd", "4th", ... "11th", "12th", "13th", "21st", ...
  function ordinal(rank) {
    var n = Number(rank) || 0;
    var tens = n % 100;
    var suffix = "th";
    if (tens < 11 || tens > 13) {
      suffix = ["th", "st", "nd", "rd"][n % 10] || "th";
    }
    return n + suffix;
  }

  // data.startAt: a date as the operator typed it (shown as it is), or a Unix
  // time (seconds or milliseconds), or nothing.
  function formatDate(value) {
    if (typeof value === "number" && value > 0) {
      var date = new Date(value > 1e12 ? value : value * 1000);
      return date.getFullYear() + "/" + (date.getMonth() + 1) + "/"
        + date.getDate();
    }
    return typeof value === "string" ? value.trim() : "";
  }

  // A character's picture ({asset, eyesight}): the full body, else the first
  // picture the game's assets have. null when none.
  function characterAsset(character) {
    var assets = (character && character.assets) || {};
    var asset = assets.full || assets[Object.keys(assets)[0]];
    return asset && asset.asset ? asset : null;
  }

  // What a row shows. A players row: its first character, its sponsor. A
  // teams row: its first member's character, its members' names.
  function describe(row, teams) {
    var members = Array.isArray(row.members) ? row.members : [];
    var characters = Array.isArray(row.characters) && row.characters.length
      ? row.characters : (row.character ? [row.character] : []);
    var first = teams ? (members[0] || {}).character : characters[0];
    var asset = characterAsset(first);
    return {
      rank: ordinal(row.rank),
      name: row.name || "",
      sub: teams
        ? members.map(function (member) { return member.name || ""; })
          .filter(Boolean).join(" / ")
        : row.sponsor || "",
      picture: resolve(asset && asset.asset),
    };
  }

  // Whether a row has anything to draw: an empty slot is not drawn.
  function filled(row) {
    return !!(row && (row.name
      || (row.members && row.members.length)
      || (row.characters && row.characters.length)
      || (row.character && row.character.assets)));
  }

  function card(shown, big) {
    var element = div(big ? "card big" : "card");
    element.appendChild(image("portrait", shown.picture));
    element.appendChild(div("shade"));
    element.appendChild(div("rank", shown.rank));
    var label = div("label");
    label.appendChild(div("name fit", shown.name));
    if (shown.sub) label.appendChild(div("sub fit", shown.sub));
    element.appendChild(label);
    return element;
  }

  function line(shown) {
    var element = div("line");
    element.appendChild(div("rank", shown.rank));
    element.appendChild(image("icon", shown.picture));
    element.appendChild(div("name fit", shown.name));
    return element;
  }

  // Squeeze a line that is too long for its box (it stays one line). Measured
  // once the fonts are in: before that, the width is another font's.
  function fitText() {
    document.querySelectorAll(".fit").forEach(function (element) {
      element.style.transform = "";
      var room = element.clientWidth;
      var needed = element.scrollWidth;
      if (room > 0 && needed > room) {
        element.style.transform = "scaleX(" + Math.max(0.5, room / needed) + ")";
      }
    });
  }

  Autona.onRender(async function (payload) {
    var data = payload.data || {};
    var config = payload.config || {};
    var teams = data.rows === "teams";
    var rows = (Array.isArray(data.players) ? data.players : []).filter(filled)
      .map(function (row) { return describe(row, teams); });

    // The operator's colors on the main parts; the others are the design's.
    var root = document.documentElement;
    root.style.setProperty("--theme", config.theme_color || "#181b21");
    root.style.setProperty("--accent", config.accent_color || "#f0a500");

    setImage("background", resolve(config.background_path));
    setImage("logo", resolve(config.main_icon_path));
    document.getElementById("tournament").textContent = data.tournamentName || "";
    document.getElementById("info").textContent = [
      data.eventName,
      formatDate(data.startAt),
      data.numEntrants ? data.numEntrants + " Entrants" : "",
    ].filter(Boolean).join("  |  ");

    // Everything drawn anew: the page is drawn again for every picture.
    var first = document.getElementById("first");
    var podium = document.getElementById("podium");
    var rest = document.getElementById("rest");
    first.textContent = "";
    podium.textContent = "";
    rest.textContent = "";
    // In the order of the rows (the operator's order: a tie keeps it).
    rows.forEach(function (shown, index) {
      if (index === 0) first.appendChild(card(shown, true));
      else if (index < 4) podium.appendChild(card(shown, false));
      else if (index < 16) rest.appendChild(line(shown));
    });

    await document.fonts.ready;
    fitText();
  });
})();
