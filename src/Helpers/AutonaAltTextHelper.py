import json
from qtpy.QtGui import *
from qtpy.QtWidgets import *
from qtpy.QtCore import *


def load_program_state():
    data_path = "./out/program_state.json"
    with open(data_path, "rt", encoding="utf-8") as data_file:
        data_json = json.loads(data_file.read())
    return (data_json)


def generate_youtube(scoreboard_id=1, use_phase_name=True, use_characters=True, replace_characters=[]):
    title_length_limit = 100    # Character limit for video titles
    data = load_program_state()
    tournament_name = data.get("tournamentInfo").get("tournamentName")
    event_name = data.get("tournamentInfo").get("eventName")
    event_date = data.get("tournamentInfo").get("startAt")
    game_name = data.get("game").get("name")
    match_data = data.get("score").get(str(scoreboard_id))
    if not match_data:
        raise ValueError(f"Scoreboard #{scoreboard_id} could not be found")
    phase_data = match_data.get("phase")
    round_data = match_data.get("match")
    team_data = match_data.get("team")
    title_long = f"[{tournament_name.upper()}] - "
    title_short = f"[{tournament_name.upper()}] - "
    game_localisation = QApplication.translate("altText", "Game:")
    description = f"""
{tournament_name.upper()}
{event_name} - {event_date}
{game_localisation} {game_name}

"""
    if phase_data and use_phase_name:
        title_long = f"[{tournament_name}] {phase_data} - "
        title_short = f"[{tournament_name}] {phase_data} - "
    else:
        title_long = f"[{tournament_name}] {round_data} - "
        title_short = f"[{tournament_name}] {round_data} - "
    if phase_data:
        description = description + f"{phase_data} - {round_data}\n"
    else:
        description = description + f"{round_data}\n"
    for team_id in team_data.keys():
        current_team_data = team_data.get(team_id)
        team_name = current_team_data.get("teamName")
        player_names = []
        player_names_with_characters = []
        player_names_with_variants = []
        if team_name:
            title_long = f'{title_long}' + team_name
            title_short = f'{title_short}' + team_name
            description = description + team_name
        else:
            player_data = current_team_data.get("player")
            for player_id in player_data.keys():
                character_names = []
                character_names_with_variants = []
                current_player_data = player_data.get(player_id)
                player_name = current_player_data.get("mergedName")
                character_data = current_player_data.get("character")
                for character_id in character_data.keys():
                    current_character_data = character_data.get(character_id)
                    if current_character_data.get("name"):
                        current_character_name = current_character_data.get("name")
                        current_character_name_with_variants = current_character_data.get("name")
                        if current_character_data.get("variant", {}).get("name"):
                            current_character_name_with_variants = f'{current_character_name} - {current_character_data.get("variant", {}).get("name")}'
                        character_names.append(current_character_name)
                        character_names_with_variants.append(current_character_name_with_variants)
                if player_name:
                    if player_name.replace(' [L]', ''):
                        player_names.append(player_name.replace(" [L]", ""))
                        if character_names:
                            player_names_with_characters.append(
                                f"{player_name.replace(' [L]', '')} ({', '.join(character_names)})")
                            player_names_with_variants.append(
                                f"{player_name.replace(' [L]', '')} ({', '.join(character_names_with_variants)})")
                        else:
                            player_names_with_characters.append(
                                player_name.replace(" [L]", ""))
                            player_names_with_variants.append(
                                player_name.replace(" [L]", ""))
            title_long = f'{title_long}' + \
                " / ".join(player_names_with_characters)
            title_short = f'{title_short}' + " / ".join(player_names)
            if use_characters:
                description = description + \
                    " / ".join(player_names_with_variants)
            else:
                description = description + \
                    " / ".join(player_names)

        if team_id == "1":
            title_long = f'{title_long} ' + \
                QApplication.translate("altText", "VS") + ' '
            title_short = f'{title_short} ' + \
                QApplication.translate("altText", "VS") + ' '
            description = f'{description} ' + \
                QApplication.translate("altText", "VS") + ' '

    description = f"{description}\n\n"
    commentator_data = data.get("commentary")
    commentator_names = []
    for commentator_id in commentator_data.keys():
        current_commentator_data = commentator_data.get(commentator_id)
        if current_commentator_data.get("mergedName"):
            commentator_names.append(
                current_commentator_data.get("mergedName"))
    if commentator_names:
        description = description + QApplication.translate(
            "altText", "Commentators:") + " " + " / ".join(commentator_names) + "\n"

    description = description + QApplication.translate("altText", "Stream powered by Autona:") + \
        " " + "https://github.com/yuya-tagu/Autona/releases"
    description = description.strip()

    for character_set in replace_characters:
        title_long = title_long.replace(character_set[0], character_set[1])
        title_short = title_short.replace(character_set[0], character_set[1])
        description = description.replace(character_set[0], character_set[1])

    if len(title_long) <= title_length_limit and use_characters:
        return (title_long, description)
    else:
        if len(title_short) > title_length_limit:
            while len(title_short) > title_length_limit - 1:
                title_short = title_short[:-1]
            return (f"{title_short}…", description)
        else:
            return (title_short, description)


if __name__ == "__main__":
    from termcolor import colored
    print(colored("TEST MODE - AutonaAltTextHelper.py", "red"))
    print("====")
    title, description = generate_youtube(1)
    print(colored("YouTube title: ", "yellow") + f"{title}")
    print(colored(f"\nDescription: \n", "yellow") + f"{description}")
