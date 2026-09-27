"""Map Polymarket tags to a small set of research categories (same table as scripts/categories.py)."""

# first matching tag wins; order matters (specific before generic)
CATEGORIES = [
    ("Mentions/Tweets", {"Mentions", "Tweet Markets", "Elon Tweets"}),
    ("Crypto", {"Crypto", "Crypto Prices", "Bitcoin", "Ethereum", "Solana", "XRP", "Ripple", "NFTs"}),
    ("Esports", {"Esports", "counter strike 2", "Dota 2", "league of legends"}),
    ("Sports", {"Sports", "Soccer", "NBA", "NFL (All)", "MLB", "NHL", "Tennis", "NCAA", "UFC", "EPL", "Games",
                "CFB (All)", "Basketball", "Hockey", "Premier League", "UCL", "Champions League", "La Liga",
                "Bundesliga", "UEL", "Olympics", "Chess", "Formula 1", "F1", "Golf", "Boxing", "Cricket"}),
    ("Geopolitics", {"Geopolitics", "Middle East", "Israel", "Iran", "Ukraine", "Russia", "China", "World",
                     "Ukraine & Russia", "Gaza", "Syria", "Venezuela"}),
    ("Elections", {"Elections", "Global Elections", "World Elections", "US Election", "deprec USA Election",
                   "Primaries", "Kamala"}),
    ("US Politics", {"Politics", "Trump", "Trump Presidency", "U.S. Politics", "Congress", "Courts"}),
    ("Econ/Finance", {"Economy", "Finance", "Fed", "Fed Rates", "Business", "Big Tech", "Tech", "Pre-Market",
                      "Stocks", "Earnings", "Economic Policy", "AI"}),
    ("Culture", {"Culture", "Pop Culture", "Movies", "Awards", "Music", "Elon Musk", "Celebrities", "TV"}),
    ("Weather/Science", {"Weather", "Science", "Climate", "Space", "Coronavirus"}),
]
NAMES = [name for name, _ in CATEGORIES] + ["Other"]

# markets priced off a measurable distribution: no favourite edge found in backtests
QUANT = {"Crypto", "Mentions/Tweets", "Weather/Science", "Esports"}


def categorize(tags, legacy_cat=None) -> str:
    tags = set(tags if tags is not None else [])
    for name, keys in CATEGORIES:
        if tags & keys:
            return name
    lc = legacy_cat.strip().lower() if isinstance(legacy_cat, str) else ""
    if "sport" in lc or "nba" in lc:
        return "Sports"
    if "crypto" in lc or "nft" in lc:
        return "Crypto"
    if "politic" in lc or "current-affairs" in lc or "ukraine" in lc:
        return "US Politics"
    return "Other"


def group(cat: str) -> str:
    return "geo" if cat == "Geopolitics" else "quant" if cat in QUANT else "event"
