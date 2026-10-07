from string import Formatter

from duecoach import strings


def fields(text: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(text) if name}


def test_both_languages_have_the_same_strings():
    assert set(strings.STRINGS["en"]) == set(strings.STRINGS["el"])


def test_placeholders_match_between_languages():
    mismatched = {
        key: (fields(en), fields(strings.STRINGS["el"][key]))
        for key, en in strings.STRINGS["en"].items()
        if fields(en) != fields(strings.STRINGS["el"][key])
    }
    assert not mismatched
