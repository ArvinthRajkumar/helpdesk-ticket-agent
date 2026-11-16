"""A deterministic nl/en/de detector from common function words.

Two uses: check that a reply is in the language the model claimed, and pick a language for
a fallback handoff when the model is unavailable. It only has to separate three languages
on a few sentences, so a word list is enough and every result can be explained.
"""

import re

_WORDS = {
    "nl": """de het een en ik je jij wij niet van voor met op dat hoe wat kan kunnen ons onze
        mijn graag bij naar ook maar geen wordt worden zijn heb hebben u uw bedankt dank
        goedemorgen hoi groet groeten vriendelijke nog al om er als dit deze moet mag wil
        zou alleen onder ga gaat klik kies daarna vervolgens tot bent jullie""",
    "en": """the a an and i you not of for with on that how what can our my please to it do
        does be are have has no this thanks thank hi your there get when why will would
        only under go click choose then until any per from or""",
    "de": """der das ein eine und ich sie nicht von für mit ist auf dass wie was kann können
        unser unsere mein bitte zu im es bei auch aber kein keine wird haben hat sind ihr
        ihre danke guten grüße gruß freundlichen noch oder wenn wo nur unter gehen klicken
        dann bis jede pro vom""",
}
# A word that appears in two lists says nothing about which language it is.
_ALL = [set(w.split()) for w in _WORDS.values()]
STOPWORDS = {
    lang: set(words.split()) - set().union(*(s for s in _ALL if s != set(words.split())))
    for lang, words in _WORDS.items()
}
TOKEN = re.compile(r"[a-zäöüßéèëï]+")


def detect(text: str, min_hits: int = 2) -> str | None:
    """The language with the most stopword hits, or None when the text gives too little
    evidence or two languages tie."""
    tokens = TOKEN.findall(text.lower())
    scores = {lang: sum(t in words for t in tokens) for lang, words in STOPWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < min_hits or top == second:
        return None
    return best
