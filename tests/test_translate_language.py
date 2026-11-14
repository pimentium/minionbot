import pytest

from minionbot.translate.language import IsAlready

ENGLISH = (
    "The city council voted on Tuesday to approve a long-awaited plan for the riverfront, and the mayor said that the "
    "decision is a sign that compromise is still possible. It will be built over the next five years with the help of "
    "residents and local businesses, which have been asking for this for a long time."
)
RUSSIAN = (
    "Городской совет во вторник проголосовал за давно ожидаемый план застройки набережной, а мэр заявил, что это решение "
    "показывает: компромисс всё ещё возможен. Строительство продлится пять лет при участии жителей и местных предприятий, "
    "которые давно этого добивались."
)
UKRAINIAN = (
    "Міська рада у вівторок проголосувала за довгоочікуваний план забудови набережної, а мер заявив, що це рішення "
    "свідчить: компроміс усе ще можливий. Будівництво триватиме п'ять років за участю мешканців і місцевих підприємств, "
    "які давно цього домагалися."
)
GERMAN = (
    "Der Stadtrat hat am Dienstag einen lange erwarteten Plan für das Flussufer beschlossen, und der Bürgermeister sagte, "
    "dass die Entscheidung zeige, dass ein Kompromiss weiterhin möglich sei. Gebaut wird über die nächsten fünf Jahre."
)


@pytest.mark.parametrize("text,language,expected", [
    (ENGLISH, "en", True), (ENGLISH, "ru", False),
    (RUSSIAN, "ru", True), (RUSSIAN, "en", False),
    (UKRAINIAN, "ru", False),  # Cyrillic but not Russian: must be translated
    (GERMAN, "en", False), (GERMAN, "ru", False),  # Latin script but not English
    ("Short text.", "en", False), ("Коротко.", "ru", False), ("", "en", False),
    (ENGLISH, "fr", False),
])
def test_already_in_the_target_language(text, language, expected):
    assert IsAlready(text, language) is expected


def test_only_the_beginning_is_inspected_and_mixed_pages_count_by_majority():
    assert IsAlready(ENGLISH * 15 + RUSSIAN * 10, "en") is True  # the sample is the first 4000 characters
    assert IsAlready(RUSSIAN + ENGLISH * 30, "ru") is False
