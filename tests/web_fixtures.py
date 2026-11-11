"""HTML pages for the web reader tests (synthetic, shaped like real news sites)."""

PARAGRAPHS = [
    "The city council voted on Tuesday to approve a long-awaited plan for the riverfront, ending nearly a decade of debate "
    "about how the former industrial district should be used by residents, businesses and visitors in the years to come.",
    "Supporters argued that the plan balances new housing with public parks, while opponents warned about rising rents and "
    "the loss of small workshops that have operated along the water for generations and employ hundreds of local people.",
    "Under the approved design, a continuous promenade will connect the old harbour to the northern bridge, with cycling "
    "lanes, playgrounds and several covered markets, and construction is expected to begin in the spring of next year.",
    "The mayor said the decision showed that careful compromise is still possible, and promised regular public updates, "
    "an independent review of every major contract and a dedicated fund for businesses that have to relocate.",
    "Environmental groups welcomed the new wetlands that will filter runoff before it reaches the river, calling them the "
    "most ambitious restoration project the region has attempted since the cleanup of the nineteen nineties.",
]


def Article(extraHead="", extraBody="", paragraphs=PARAGRAPHS):
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<!doctype html><html lang="en"><head><title>Council approves riverfront plan | Example News</title>
<meta property="og:title" content="Council approves riverfront plan">
<meta property="og:site_name" content="Example News"><meta property="og:image" content="https://cdn.example.com/lead.jpg">
<meta name="description" content="After a decade of debate the council backed the riverfront plan.">
<meta name="author" content="Jane Reporter"><meta property="article:published_time" content="2026-11-09T08:30:00Z">{extraHead}
<script>window.ads = ["BUYNOW-SCRIPT"];</script><style>.x{{color:red}}</style></head>
<body><header><nav><a href="/">Home</a> | <a href="/sports">Sports</a> | <a href="/login">Log in</a></nav></header>
<div class="advert" id="ad-top">ADVERTISEMENT: BUY CHEAP WATCHES NOW</div>
<div class="cookie-banner">We use cookies. Accept all cookies</div>
<main><article><h1>Council approves riverfront plan</h1>
<p class="byline">By Jane Reporter</p>
<figure><img src="/images/river.jpg" alt="The river at dusk"><figcaption>The river at dusk</figcaption></figure>
{body}
<blockquote>We did this together, said the mayor.</blockquote>
<ul><li>New promenade</li><li>Covered markets</li></ul>
<p>The complete proposal, including maps, budgets and timelines, can be read in the <a href="/plan.pdf">full plan</a> published by the council, and a second document <a href="javascript:alert(1)">with annexes</a> is also available for residents who want every detail of the decision.</p>
{extraBody}
</article></main>
<aside class="related"><h3>Related stories</h3><ul><li><a href="/x">SIDEBAR-TEASER story one</a></li></ul></aside>
<footer>Copyright 2026 Example News. Terms. Privacy. FOOTER-LEGAL</footer></body></html>"""


PAYWALL_MARKER = '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
TEASER = Article(
    extraHead=PAYWALL_MARKER,
    paragraphs=[PARAGRAPHS[0]],
    extraBody='<div class="paywall"><p>Subscribe to continue reading this article.</p></div>',
)
