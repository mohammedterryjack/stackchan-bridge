"""Japanese text -> spoken-friendly Hepburn romaji (ASCII only) for Stack-chan's English TTS voice.

Uses fugashi (MeCab) + unidic-lite, baked into the image, for word splits and kana readings; particles
は/へ/を are read wa/e/o. Raises ValueError when a token has no reading, so nothing Japanese leaks into speech."""

from __future__ import annotations

import re
import unicodedata

import fugashi

_BASE = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko", "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so", "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to", "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho", "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo", "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro", "ワ": "wa", "ヰ": "i", "ヱ": "e", "ヲ": "o", "ン": "n",
    "ヴ": "vu", "ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o", "ヮ": "wa", "ヵ": "ka", "ヶ": "ke",
}
_SMALL_Y = {"ャ": "a", "ュ": "u", "ョ": "o"}
_PAIRS = {
    "シェ": "she", "ジェ": "je", "チェ": "che", "ティ": "ti", "ディ": "di", "トゥ": "tu", "ドゥ": "du", "ファ": "fa",
    "フィ": "fi", "フェ": "fe", "フォ": "fo", "ウィ": "wi", "ウェ": "we", "ウォ": "wo", "ヴァ": "va", "ヴィ": "vi",
    "ヴェ": "ve", "ヴォ": "vo", "ツァ": "tsa", "イェ": "ye", "デュ": "dyu", "テュ": "tyu", "フュ": "fyu",
}
_PUNCT = {"。": ".", "、": ",", "？": "?", "！": "!", "・": " ", "「": "", "」": "", "『": "", "』": "", "（": "(", "）": ")",
          "…": "...", "〜": "", "～": "", "：": ":", "　": " "}
_PARTICLE = {"ハ": "wa", "ヘ": "e", "ヲ": "o"}
_READ = {"私": "ワタシ", "私たち": "ワタシタチ", "私達": "ワタシタチ"}
_FIXED = {"こんにちは": "konnichiwa", "こんばんは": "konbanwa", "今日は": "konnichiwa", "今晩は": "konbanwa"}

_tagger: fugashi.Tagger | None = None


def _kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def kana_to_romaji(kana: str) -> str:
    """Katakana or hiragana -> Hepburn ASCII. Raises ValueError on characters that are not kana."""
    s, out, i = _kata(kana), [], 0
    gem = False
    while i < len(s):
        c, two = s[i], s[i:i + 2]
        if c == "ッ":
            gem, i = True, i + 1
            continue
        if c == "ー":
            if out and out[-1][-1:] in "aeiou":
                out.append(out[-1][-1])
            i += 1
            continue
        if two in _PAIRS:
            r, i = _PAIRS[two], i + 2
        elif len(two) == 2 and two[1] in _SMALL_Y and c in _BASE and _BASE[c].endswith("i") and c != "イ":
            b = _BASE[c][:-1]
            r = (b if b in ("sh", "ch", "j") else b + "y") + _SMALL_Y[two[1]]
            i += 2
        elif c in _BASE:
            r, i = _BASE[c], i + 1
        else:
            raise ValueError(f"cannot read {c!r} as kana")
        if gem:
            r = ("t" + r) if r.startswith("ch") else (r[0] + r if r[0] not in "aeiou" else r)
            gem = False
        if out and out[-1] == "n" and r[0] in "aeiouy":
            out[-1] = "n'"
        out.append(r)
    if gem:
        out.append("t")
    return "".join(out)


def _tag():
    global _tagger
    if _tagger is None:
        _tagger = fugashi.Tagger()
    return _tagger


def has_japanese(s: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]", s))


def to_romaji(text: str) -> str:
    """Japanese sentence -> spaced romaji, e.g. 駅はどこですか？ -> 'eki wa doko desu ka?'."""
    text = unicodedata.normalize("NFKC", text).replace("\u3000", " ")
    for k, v in _FIXED.items():
        if re.fullmatch(re.escape(k) + r"[。！!？?、\s]*", text.strip()):
            text = text.replace(k, v)
    words: list[str] = []
    glue = False
    prev_pos = ""
    for w in _tag()(text):
        surf, f = w.surface, w.feature
        pos1, pos2 = f.pos1, f.pos2
        if surf in _PUNCT or pos1 == "補助記号":
            p = _PUNCT.get(surf, surf if not has_japanese(surf) else "")
            if p.strip():
                if words and p in ".,?!:)...":
                    words[-1] += p
                else:
                    words.append(p)
            glue = False
            continue
        if not has_japanese(surf):
            words.append(surf)
            glue, prev_pos = False, pos1
            continue
        kana = f.kana if f.kana and f.kana != "*" else (surf if re.fullmatch(r"[\u3040-\u30ffー]+", surf) else "")
        kana = _READ.get(surf, kana)
        if not kana:
            raise ValueError(f"no reading for {surf!r}")
        r = _PARTICLE[kana] if pos1 == "助詞" and kana in _PARTICLE else kana_to_romaji(kana)
        attach = glue or (
            pos1 == "助動詞" and prev_pos in ("動詞", "助動詞", "形容詞") and kana not in ("デス", "デショウ") and not (kana == "ダ" and prev_pos == "形容詞")
        ) or (pos1 == "助詞" and pos2 == "接続助詞" and kana in ("テ", "デ") and prev_pos in ("動詞", "助動詞", "形容詞")) or (
            pos1 == "接尾辞" and words
        )
        if attach and words:
            words[-1] += r
        else:
            words.append(r)
        glue = kana.endswith("ッ") or pos1 == "接頭辞"
        prev_pos = pos1
    out = re.sub(r"\s+", " ", " ".join(words)).strip()
    if has_japanese(out) or not out.isascii():
        raise ValueError(f"romaji conversion left non-ASCII text in {out!r}")
    return out
