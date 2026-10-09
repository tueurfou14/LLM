from cerveau.tools.web import html_to_text, parse_duckduckgo

PAGE = """<html><head><title> Doc FastAPI </title><style>x{}</style></head>
<body><nav>menu</nav><h1>Installation</h1><p>Utilisez <code>pip</code> ou   uv.</p>
<script>alert(1)</script><pre>uv pip install fastapi
uvicorn</pre><p>Fin.</p></body></html>"""

DDG = '''<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffastapi.tiangolo.com%2F&amp;rut=abc">FastAPI</a>
<a class="result__snippet" href="x">FastAPI framework, <b>high</b> performance</a></div>
<div class="result"><a class="result__a" href="https://docs.python.org/3/">Python docs</a>
<a class="result__snippet" href="y">Official &amp; complete</a></div>'''


def test_html_to_text_keeps_content_and_drops_noise():
    title, text = html_to_text(PAGE)
    assert title == "Doc FastAPI"
    assert "# Installation" in text and "Utilisez pip ou uv." in text
    assert "menu" not in text and "alert" not in text
    assert "uv pip install fastapi\nuvicorn" in text


def test_parse_duckduckgo_decodes_redirects_and_snippets():
    results = parse_duckduckgo(DDG, 5)
    assert results[0] == {"titre": "FastAPI", "url": "https://fastapi.tiangolo.com/",
                          "extrait": "FastAPI framework, high performance"}
    assert results[1]["url"] == "https://docs.python.org/3/" and results[1]["extrait"] == "Official & complete"


def test_default_registry_has_web_tools():
    from cerveau.tools import default_registry

    names = default_registry().names()
    assert "web_search" in names and "web_fetch" in names


BING = """<ol id="b_results"><li class="b_algo"><h2><a href="https://www.sqlalchemy.org/">SQLAlchemy</a></h2>
<div class="b_caption"><p>The Python SQL <strong>toolkit</strong></p></div></li></ol>"""


def test_parse_bing():
    from cerveau.tools.web import parse_bing

    assert parse_bing(BING, 5) == [{"titre": "SQLAlchemy", "url": "https://www.sqlalchemy.org/",
                                   "extrait": "The Python SQL toolkit"}]
