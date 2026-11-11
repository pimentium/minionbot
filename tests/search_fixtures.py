"""SerpApi responses (shaped like the documented ones; texts are placeholders)."""


def Organic(count=3):
    return [
        {
            "position": number,
            "title": f"Result {number} - Example",
            "link": f"https://www.example{number}.com/page",
            "redirect_link": "https://www.google.com/url?q=x",
            "displayed_link": f"https://www.example{number}.com › page",
            "snippet": f"Snippet for result {number}.",
            "source": "Example",
        }
        for number in range(1, count + 1)
    ]


AI_OVERVIEW = {
    "text_blocks": [
        {"type": "heading", "snippet": "What is Python?"},
        {"type": "paragraph", "snippet": "Python is a high-level programming language.", "reference_indexes": [0]},
        {
            "type": "list",
            "list": [
                {"title": "Readable", "snippet": "The syntax is close to plain English.", "reference_indexes": [1]},
                {"snippet": "No lead-in here."},
            ],
        },
        {"type": "table", "table": [["a", "b"]]},  # unsupported kinds are skipped
    ],
    "references": [
        {"title": "Python.org", "link": "https://www.python.org/", "snippet": "x", "source": "Python", "index": 0},
        {"title": "Docs", "link": "https://docs.python.org/3/", "snippet": "y", "source": "Python", "index": 1},
    ],
}


def Response(organic=3, ai=None, aiToken=None):
    data = {"search_information": {"query_displayed": "q"}, "organic_results": Organic(organic)}
    if ai is not None:
        data["ai_overview"] = ai
    if aiToken:
        data["ai_overview"] = {"page_token": aiToken, "serpapi_link": "https://serpapi.com/search.json?engine=google_ai_overview"}
    return data
