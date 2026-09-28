"""
projects_data.py — single source of truth for every shipped project.

Each entry doubles as a "blog post" on the home page and an entry on the
/portfolio page. Add a new project here and it shows up in both places
automatically, newest first.
"""

PROJECTS = [
    {
        "slug": "imposter",
        "title": "The Imposter",
        "date": "2026-09-28",
        "screenshot": "blog/imposter.png",
        "emoji": "\U0001F575️",
        "tags": ["Python", "Flask", "SocketIO", "Party Game"],
        "summary": (
            "A real time social deduction party game. Everyone shares one secret "
            "word from a shown category, except a randomly chosen imposter, who "
            "only sees the category. Players give one word clues in turn, then "
            "vote on the faker. Get caught and the imposter gets one shot to "
            "guess the word and steal the win."
        ),
        "meta": [
            ("Stack", "Flask · SocketIO · JS"),
            ("Players", "3 – 12"),
        ],
        "links": [
            {"label": "Play a Round", "url": "/imposter/"},
        ],
    },
    {
        "slug": "draft-race",
        "title": "100 Yard Dash - Fantasy Football Draft Order",
        "date": "2026-07-20",
        "screenshot": "blog/draft-race.png",
        "emoji": "\U0001F3C8",
        "tags": ["Python", "Flask", "SocketIO", "Canvas"],
        "summary": (
            "Pick 2–32 players, name them, then send them sprinting down a "
            "football field. Everyone runs at a different randomized pace and "
            "the finish order becomes your draft order. Share a live watch link "
            "so the whole league can spectate in real time, plus a results link "
            "for the final board. Deterministic seeded races render identically "
            "for every viewer."
        ),
        "meta": [
            ("Stack", "Flask · SocketIO · Canvas · JS"),
            ("Players", "2 – 32"),
        ],
        "links": [
            {"label": "Run the Race", "url": "/draft/"},
        ],
    },
    {
        "slug": "nfl-draft",
        "title": "Playbooking - NFL Stat Draft Game",
        "date": "2026-04-22",
        "screenshot": "blog/nfl-draft.png",
        "emoji": "\U0001F3C8",
        "tags": ["Python", "Flask", "Football"],
        "summary": (
            "Same format as the baseball draft, but powered by 25 years of NFL "
            "offensive stats. Each round gives you a stat category, passing "
            "yards, receiving TDs, passer rating, and 5 slots tied to teams, "
            "divisions, or eras. Pick the right player + year combo to rack up "
            "the highest total."
        ),
        "meta": [
            ("Stack", "Flask · Python · nfl_data_py"),
            ("Data", "2000 – 2025"),
        ],
        "links": [
            {"label": "Play Now", "url": "/nfl/"},
            {"label": "Multiplayer", "url": "/nfl-mp/"},
        ],
    },
    {
        "slug": "baseball-draft",
        "title": "Ballparking - MLB Stat Draft Game",
        "date": "2026-02-25",
        "screenshot": "blog/baseball-draft.png",
        "emoji": "⚾",
        "tags": ["Python", "Flask", "Baseball"],
        "summary": (
            "A stat based baseball knowledge game. Each round gives you a stat "
            "category and 5 slots, each tied to a team, division, or era. "
            "Search for players, pick the year you think was their peak, and "
            "rack up the highest total. Powered by historical MLB data going "
            "back to 1990."
        ),
        "meta": [
            ("Stack", "Flask · Python · pybaseball"),
            ("Data", "1990 – 2025"),
        ],
        "links": [
            {"label": "Play Now", "url": "/game/"},
            {"label": "Multiplayer", "url": "/mp/"},
        ],
    },
    {
        "slug": "wolves",
        "title": "Wolf Pack Genetic Algorithm",
        "date": "2026-05-06",
        "screenshot": "blog/wolves.png",
        "emoji": "\U0001F43A",
        "tags": ["Machine Learning", "Genetic Algorithm", "Simulation"],
        "summary": (
            "A machine learning genetic algorithm where wolves learn to hunt "
            "entirely through natural selection, no hunting rules are "
            "hardcoded. Each wolf has a six-gene genome biasing its movement "
            "toward prey, packmates, or away from the bear. The top performers "
            "each generation pass on mutated copies of their genes. Pack "
            "hunting emerges on its own once cooperation proves more rewarding "
            "than going solo."
        ),
        "meta": [
            ("Stack", "JS · HTML Canvas"),
            ("Genes", "Attraction · Repulsion · Speed · Perception"),
        ],
        "links": [
            {"label": "Run Simulation", "url": "/wolves"},
        ],
    },
    {
        "slug": "draft-randomizer",
        "title": "Dinger - MLB Draft Randomizer",
        "date": "2026-02-26",
        "screenshot": "blog/draft-randomizer.png",
        "emoji": "⚾",
        "tags": ["Python", "Flask", "Baseball"],
        "summary": (
            "A simple randomizer used to determine your fantasy draft order. "
            "Pick between 6–12 players, name them (or randomize), then swing "
            "away. Players are scored by home-run distance, using real-time "
            "physics done entirely on the front end."
        ),
        "meta": [
            ("Stack", "Flask · Python · JS"),
            ("Stats", "Distance · Dingers"),
        ],
        "links": [
            {"label": "Simulate", "url": "/draftorder"},
        ],
    },
]
