from functools import reduce
from msgpack import unpackb, packb


def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys.split("."), dictionary)


def deep_set(dictionary, keys, value):
    d = dictionary
    for key in keys.split(".")[:-1]:
        if key not in d:
            d[key] = {}
        d = d[key]
    d[keys.split(".")[-1]] = value


def deep_unset(dictionary, keys):
    d = dictionary
    for key in keys.split(".")[:-1]:
        if key not in d:
            d[key] = {}
        d = d[key]
    if keys.split(".")[-1] in d:
        del d[keys.split(".")[-1]]

def deep_clone(dictionary):
    return unpackb(packb(dictionary), strict_map_key=False)


def qvariantmap_copy(value):
    """A deep copy of `value` with every dict's keys in QVariantMap order.

    Data that used to be read back out of a Qt model had made a round trip
    through QVariantMap, i.e. a QMap: keys sorted by UTF-16 code unit, at
    every level. Two outputs carry that order on the wire, because both are
    serialised in insertion order: the Web readers' Socket.IO payloads
    (get_characters / get_variants, ADR-005 S1b) and the player slot's
    character / country / region values in the state, i.e. program_state.json
    and the Socket.IO delta (S1d). Their builders reproduce it so the bytes
    stay unchanged. Lists keep their order, and come back as lists, as
    deep_clone's do.

    Only a str-keyed dict and a list convert (to QVariantMap / QVariantList).
    A dict with any other key, and a tuple, PySide6 keeps as an opaque
    Python object, so nothing inside them is reordered: they are copied as
    is (deep_clone -- a tuple comes back as a list, the same JSON).
    """
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: qvariantmap_copy(value[key])
                    for key in sorted(value, key=lambda k: k.encode("utf-16-be"))}
        # Not a QVariantMap candidate: copied as is.
        return deep_clone(value)
    if isinstance(value, tuple):
        # Not a QVariantList either (PySide6 6.11, measured): copied as is.
        return deep_clone(value)
    if isinstance(value, list):
        return [qvariantmap_copy(item) for item in value]
    return value

