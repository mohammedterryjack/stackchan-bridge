"""Builds hf://datasets/MTerryJack/japanese-phrases from Tatoeba per-language exports (CC BY 2.0 FR).
Run in a scratch dir holding jpn_sentences.tsv.bz2, jpn-eng_links.tsv.bz2 and eng_sentences.tsv.bz2 from
https://downloads.tatoeba.org/exports/per_language/ ; needs fugashi, unidic-lite, pandas, pyarrow. Writes train.parquet."""
import bz2, csv, re, sys, collections
import pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))  # repo root: japanese_romaji.py
from japanese_romaji import to_romaji

def tsv(p):
    with bz2.open(p, "rt", encoding="utf-8") as f:
        for line in f:
            yield line.rstrip("\n").split("\t")

jpn = {int(r[0]): r[2] for r in tsv("jpn_sentences.tsv.bz2") if len(r) >= 3}
links = collections.defaultdict(list)
for r in tsv("jpn-eng_links.tsv.bz2"):
    a, b = int(r[0]), int(r[1])
    if a in jpn:
        links[a].append(b)
need = {b for v in links.values() for b in v}
eng = {}
for r in tsv("eng_sentences.tsv.bz2"):
    i = int(r[0])
    if i in need:
        eng[i] = r[2]
print("jpn", len(jpn), "linked", len(links), "eng", len(eng))

BAD = re.compile(r"\b(sex|sexy|kill|killed|suicide|drug|drugs|naked|rape|fuck|shit|damn|bitch|hell|gun|murder|die|dead|bomb|porn|kiss|drunk|beer|wine|sake|whisky|cigarette|smoke)\b", re.I)
TOPICS = [
    ("greetings", r"\b(hello|hi|good (morning|afternoon|evening|night)|goodbye|bye|see you|nice to meet|how are you|welcome|thank|thanks|sorry|excuse me|please|you're welcome|pleased to meet|my name)\b"),
    ("food", r"\b(eat|ate|eaten|food|drink|drank|water|tea|coffee|milk|rice|bread|breakfast|lunch|dinner|delicious|hungry|thirsty|restaurant|cook|meal|fish|meat|apple|fruit|vegetable|egg|sushi|sugar|cake)\b"),
    ("travel", r"\b(station|train|bus|taxi|airport|plane|hotel|ticket|where is|map|left|right|straight|travel|trip|car|road|street|go to|went to|subway|bicycle|walk)\b"),
    ("shopping", r"\b(buy|bought|price|how much|money|yen|dollar|shop|store|cheap|expensive|sell|pay|cost)\b"),
    ("time", r"\b(time|o'clock|today|tomorrow|yesterday|week|month|year|morning|night|now|later|early|late|monday|tuesday|wednesday|thursday|friday|saturday|sunday|hour|minute)\b"),
    ("weather", r"\b(weather|rain|raining|snow|sunny|cloudy|hot|cold|warm|cool|wind|windy)\b"),
    ("family", r"\b(mother|father|mom|dad|sister|brother|family|son|daughter|parents|wife|husband|grandmother|grandfather|baby|child|children|friend)\b"),
    ("feelings", r"\b(happy|sad|angry|tired|sleepy|love|like|hate|afraid|scared|glad|lonely|fun|bored|worried|sick|well)\b"),
    ("school and work", r"\b(school|student|teacher|study|studied|learn|homework|class|book|read|write|work|job|office|company|english|japanese|language)\b"),
    ("home", r"\b(house|home|room|door|window|bed|sleep|bath|kitchen|tv|television|phone|clean)\b"),
    ("numbers", r"\b(one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|how many)\b|\d"),
]
TOPICS = [(n, re.compile(p, re.I)) for n, p in TOPICS]
rows, seen, fails = [], set(), 0
for jid, eids in links.items():
    ja = jpn[jid].strip()
    if not (2 <= len(ja) <= 18) or ja in seen:
        continue
    cands = sorted({eng[e].strip() for e in eids if e in eng}, key=len)
    if not cands:
        continue
    en = cands[0]
    if len(en) > 70 or BAD.search(en):
        continue
    try:
        ro = to_romaji(ja)
    except ValueError:
        fails += 1
        continue
    if not ro or len(ro) > 90:
        continue
    seen.add(ja)
    topic = next((n for n, rx in TOPICS if rx.search(en)), "general")
    level = "beginner" if len(ro.split()) <= 4 else "intermediate"
    rows.append(dict(id=jid, english=en, romaji=ro, japanese=ja, topic=topic, level=level,
                     eng_id=min(e for e in eids if e in eng and eng[e].strip() == en), source="Tatoeba (CC BY 2.0 FR)"))
df = pd.DataFrame(rows).sort_values("id").reset_index(drop=True)
print("rows", len(df), "fails", fails)
print(df.topic.value_counts()); print(df.level.value_counts())
df.to_parquet("train.parquet", index=False)
print(df.sample(12, random_state=1)[["english", "romaji", "topic"]].to_string())
