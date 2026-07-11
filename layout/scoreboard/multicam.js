LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  Start = async () => {
  };

  Update = async (event) => {
    let data = event.data;
    let oldData = event.oldData;

    if(
      Object.keys(_.get(data, "score", {})).length != Object.keys(_.get(oldData, "score", {})).length ||
      $(".multicam_container").html().trim() == ""
    ){
      let scoreboardNumber = Object.keys(_.get(data, "score", {})).length;

      let html = "";
      
      for(let i=0; i<scoreboardNumber; i+=1) {
        if(["ruleset", "bracket_type"].includes(Object.keys(_.get(data, "score", {}))[i])) continue;
        html+=`<div class="wrap"><iframe src="./ssbultimate.html?scoreboardNumber=${Object.keys(_.get(data, "score", {}))[i]}&mini=true"></iframe></div>`
      }
      
      $(".multicam_container").html(html);

      $("iframe").each((id, el)=>{
          console.log(el)
          el.onload = ()=>{
            let head = $(el).contents().find("head");
            console.log(head)
            $(head).append($("<link/>", {
              rel: "stylesheet",
              href: "./multicam.css",
              type: "text/css"
            }));
          }
      })
      
      let columns = Math.ceil(Math.sqrt(scoreboardNumber-2));
      
      let fullWidth = $(".multicam_container").width()
        - parseFloat($(".multicam_container").css("padding-left"))
        - parseFloat($(".multicam_container").css("padding-right"));

      let workingWidth = fullWidth;

      let gap = parseFloat($(".multicam_container").css("column-gap"));

      if(gap){
        workingWidth -= (columns-1) * gap;
      }

      $("iframe").css({
        "transform": `scale(${(workingWidth/1920)/columns})`
      })

      $(".wrap").css({
        "width": `${workingWidth/columns}px`,
        "height": `${(workingWidth/1920)/columns*1080}px`,
      })
    }

    // Prefer the operator-controlled display name; fall back to the raw
    // tournament name only when displayTournamentName was never set (an
    // explicit empty string means "intentionally hidden" and is respected).
    const ti = data.tournamentInfo || {};
    const displayTournamentName = ti.displayTournamentName !== undefined
      ? ti.displayTournamentName
      : (ti.tournamentName || "");
    SetInnerHtml($(`.tournament_name`),
      displayTournamentName ?
        `<span class="faded">${displayTournamentName}</span>
        <span class="divider">/</span>`
        :
        displayTournamentName
    );
    SetInnerHtml($(`.event_name`), data.tournamentInfo.eventName);
  };
});
