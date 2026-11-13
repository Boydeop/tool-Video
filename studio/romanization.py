"""Best-effort Korean Hangul transliteration for *legacy* unanswered romanization.

This is orthographic Revised Romanization, not a full phonological engine.
New AI questions should supply a human-reviewed romanization. Flag the fallback
as a suggestion, never promise that liaison/assimilation is fully represented.
"""
CHOSEONG=("g","kk","n","d","tt","r","m","b","pp","s","ss","","j","jj","ch","k","t","p","h")
JUNGSEONG=("a","ae","ya","yae","eo","e","yeo","ye","o","wa","wae","oe","yo",
            "u","wo","we","wi","yu","eu","ui","i")
JONGSEONG=("","k","k","ks","n","nj","nh","t","l","lk","lm","lb","ls","lt",
            "lp","lh","m","p","ps","t","t","ng","t","t","k","t","p","t")

def approximate_romanization(word):
    parts=[]
    for letter in word:
        code=ord(letter)
        if 0xAC00<=code<=0xD7A3:
            num=code-0xAC00
            cho=num//588
            jung=(num%588)//28
            jong=num%28
            parts.append(CHOSEONG[cho]+JUNGSEONG[jung]+JONGSEONG[jong])
        elif letter.isspace() or letter in "-.,!?":
            parts.append(letter)
        else:
            return ""
    return "".join(parts)
