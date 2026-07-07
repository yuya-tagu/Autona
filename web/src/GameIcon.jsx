import {BASE_URL} from "./env";
import {useState} from "react";

/** @param {{game: TSHGameInfo, fixedWidth: boolean, size: number}} props */
export function GameIcon({game, fixedWidth=false, size=48, ...rest}) {
  const {errored, setErrored} = useState(false);

  const logoUrl = `${BASE_URL}/user_data/games/${game.codename}/base_files/logo.png`;
  return <div {...rest}>
    {errored
      ? <span>{game.codename}</span>
      : <img
        alt="Game logo"
        src={logoUrl}
        {...(fixedWidth ? {width: size} : {height: size})}
        onError={(e) => setErrored(true)}
      />
    }
  </div>
}
