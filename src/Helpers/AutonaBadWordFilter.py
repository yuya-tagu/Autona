"""Compatibility front for the profanity filter (ADR-005 S3e-1).

The filter lives in src/core/BadWordFilter.py now, Qt-free, with the program
locale and the country tables injected by bootstrap_core(), which also
builds and initialises `BadWordFilter.instance`. The widgets that censor
(the player card, the commentary panel) keep calling
`AutonaBadWordFilter.Censor(...)`; this forwards to that instance.
"""
from typing import Dict, Union

from ..core.BadWordFilter import BadWordFilter


class AutonaBadWordFilter:
    @staticmethod
    def Censor(value: Union[Dict, str, None], countryCode2: str = None):
        return BadWordFilter.instance.Censor(value, countryCode2)

    @staticmethod
    def CensorString(value: str, playerCountry: str = None):
        return BadWordFilter.instance.CensorString(value, playerCountry)

    @staticmethod
    def CensorDict(dictionary: dict):
        return BadWordFilter.instance.CensorDict(dictionary)
