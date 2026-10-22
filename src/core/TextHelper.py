"""Text normalization shared by the lookups that match user-facing names.

Qt-free and import-inert like the rest of src/core. One definition so the
folding these lookups depend on cannot drift between copies: the bad-word
filter folds both its word lists and the tested name with it, and the
Start.gg city -> state fallback (AutonaCountryHelper.FindState) folds the
lookup input the way scripts/gen_city_to_state.py folded the table keys.
"""
import unicodedata


def remove_accents_lower(input_str):
    """Strip combining marks after NFKD decomposition, then lowercase.

    "Ōsaka" -> "osaka". Does not strip whitespace (the city table generator
    does that on its side; FindState strips each part before the lookup).
    """
    nfkd_form = unicodedata.normalize('NFKD', input_str)
    return u"".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()
