LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  let startingAnimation = gsap
    .timeline({ paused: true })
    .from($(".h2h_record"), { autoAlpha: 0 });

  var playersRecentSets = null;
  var p1Name = "";
  var p2Name = "";

  // Safely read a scoreboard player name. Missing nodes yield an empty string.
  function GetPlayerName(scoreData, team) {
    try {
      return String(scoreData.team[team].player["1"].name || "").trim();
    } catch (e) {
      return "";
    }
  }

  // Only these fields drive this layout, so diff against them alone.
  function GetRelevantData(scoreData) {
    if (scoreData == null) return null;
    return {
      recent_sets: scoreData.recent_sets,
      p1: GetPlayerName(scoreData, "1"),
      p2: GetPlayerName(scoreData, "2"),
    };
  }

  Start = async (event) => {
    startingAnimation.restart();
  };

  Update = async (event) => {
    let data = event.data;
    let oldData = event.oldData;

    let scoreData = data.score ? data.score[window.scoreboardNumber] : null;
    if (scoreData == null) return;

    let oldScoreData = oldData.score
      ? oldData.score[window.scoreboardNumber]
      : null;

    if (
      !oldData.score ||
      JSON.stringify(GetRelevantData(oldScoreData)) !=
        JSON.stringify(GetRelevantData(scoreData))
    ) {
      playersRecentSets = scoreData.recent_sets;
      p1Name = GetPlayerName(scoreData, "1");
      p2Name = GetPlayerName(scoreData, "2");
    }

    let isDone = playersRecentSets != null && playersRecentSets.state == "done";
    let sets = isDone && playersRecentSets.sets ? playersRecentSets.sets : [];

    // Same semantics as the desktop stats widget: winner 0 == P1 took the set.
    let p1Wins = 0;
    let p2Wins = 0;

    for (const _set of sets) {
      if (_set.winner == 0) p1Wins += 1;
      else if (_set.winner == 1) p2Wins += 1;
    }

    let total = p1Wins + p2Wins;

    let winsHtml = (wins) => {
      if (!isDone) return `-`;
      let rate = total > 0 ? `${Math.round((wins / total) * 100)}%` : "-";
      return `${wins} <span class="h2h_rate">(${rate})</span>`;
    };

    SetInnerHtml($(`.h2h_player.p1 .h2h_name`), p1Name);
    SetInnerHtml($(`.h2h_player.p2 .h2h_name`), p2Name);
    SetInnerHtml($(`.h2h_player.p1 .h2h_wins`), winsHtml(p1Wins));
    SetInnerHtml($(`.h2h_player.p2 .h2h_wins`), winsHtml(p2Wins));
    SetInnerHtml($(`.h2h_total`), isDone ? `[${total} sets]` : "");

    let setsHtml = "";

    if (
      playersRecentSets == null ||
      (playersRecentSets.state == "done" && sets.length == 0)
    ) {
      setsHtml = `No sets found`;
    } else if (!isDone) {
      setsHtml += `<div class="lds-ring"><div></div><div></div><div></div><div></div></div>`;
    } else {
      for (const _set of sets.slice(0, 5)) {
        setsHtml += `
            <div class="set_container">
              <div class="${_set.winner == 0 ? "set_winner" : "set_loser"}">
                ${_set.score[0]}
              </div>
              <div class="set_info">
                <div class="set_title">
                  ${_set.online ? `<div class="wifi_icon"></div>` : ""}
                  ${_set.tournament}
                </div>
                <div class="set_meta">
                  <span class="set_date">
                    ${new Date(_set.timestamp * 1000).toLocaleDateString(
                      "en-US",
                      {
                        month: "short",
                        day: "2-digit",
                        year: "numeric",
                      }
                    )}
                  </span>
                  <span class="set_round">${_set.round ? _set.round : ""}</span>
                </div>
              </div>
              <div class="${_set.winner == 1 ? "set_winner" : "set_loser"}">
                ${_set.score[1]}
              </div>
            </div>
          `;
      }
    }

    SetInnerHtml($(`.h2h_sets_content`), setsHtml);
  };
});
