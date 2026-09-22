from html.parser import HTMLParser
from pathlib import Path


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append(tag)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag in {"link", "script"}:
            self.links.append(values.get("href") or values.get("src"))


def test_static_landing_page_has_complete_local_assets_and_landmarks():
    root = Path(__file__).parents[1] / "site"
    parser = PageParser()
    parser.feed((root / "index.html").read_text())

    assert {"header", "nav", "main", "footer", "h1"}.issubset(parser.tags)
    assert {"profiles", "proof", "install", "vision"}.issubset(parser.ids)
    assert parser.links == ["styles.css", "app.js"]
    assert all((root / asset).is_file() for asset in parser.links)
