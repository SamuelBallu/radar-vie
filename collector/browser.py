"""Reads Business France offer pages the way a person does: in a real browser, one page at a time.

The site is a JavaScript app, so the text only exists once a browser has rendered it. This module
drives the Google Chrome already installed on the Mac (no extra browser download), never calls the
site's own API, and waits between pages.
"""

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass

from playwright.sync_api import Page, sync_playwright

BASE = "https://mon-vie-via.businessfrance.fr"
PAUSE_SECONDS = 2.0  # between two page loads: a person's pace, not a crawler's


@dataclass
class Card:
    id: str
    company: str
    title: str
    location: str  # "ITALIE - BOLOGNE"
    kind: str  # "VIE" or "VIA"
    months: int | None
    excerpt: str


def _reject_cookies(page: Page) -> None:
    for label in ("Tout refuser", "Reject all", "Continuer sans accepter"):
        button = page.get_by_role("button", name=label)
        if button.count():
            try:
                button.first.click(timeout=2000)
            except Exception:
                pass
            return


def _cards(page: Page) -> list[Card]:
    raw = page.eval_on_selector_all(
        ".offer-content",
        """nodes => nodes.map(n => ({
            href: n.querySelector('a[href^="/offres/"]')?.getAttribute('href') || '',
            company: n.querySelector('.organization-name')?.innerText || '',
            title: n.querySelector('.mission-title')?.innerText || '',
            location: n.querySelector('.location')?.innerText || '',
            excerpt: n.querySelector('.mission-excerpt')?.innerText || '',
            meta: [...n.querySelectorAll('.meta-list li')].map(li => li.innerText.trim()),
        }))""",
    )
    cards = []
    for c in raw:
        match = re.search(r"/offres/(\d+)", c["href"])
        if not match:
            continue
        months = next((int(m.group(1)) for x in c["meta"] if (m := re.search(r"(\d+)\s*mois", x))), None)
        kind = next((x for x in c["meta"] if x in ("VIE", "VIA")), "")
        cards.append(Card(match.group(1), c["company"].strip(), c["title"].strip(), c["location"].strip(), kind, months, c["excerpt"].strip()))
    return cards


class Reader:
    """Use as a context manager: one browser for the whole run, closed at the end."""

    def __enter__(self) -> "Reader":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=True)
        self._context = self._browser.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900})
        self.page = self._context.new_page()
        self._last = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    def _goto(self, url: str) -> None:
        wait = PAUSE_SECONDS - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        self._last = time.monotonic()
        _reject_cookies(self.page)

    def list_new_cards(self, search_url: str, seen: set[str], max_pages: int = 60) -> tuple[list[Card], int]:
        """Cards from newest to oldest, until a whole batch of 6 is already known (or the list ends).
        Returns the unseen cards and the number of cards read."""
        self._goto(search_url)
        self.page.wait_for_selector(".offer-content", timeout=30000)
        shown = 0
        for _ in range(max_pages):
            cards = _cards(self.page)
            batch = cards[shown:]
            if not batch or all(c.id in seen for c in batch):
                break
            shown = len(cards)
            more = self.page.locator("a.see-more-btn")
            if not more.count():
                break
            time.sleep(PAUSE_SECONDS)
            more.first.click()
            try:
                self.page.wait_for_function(f"document.querySelectorAll('.offer-content').length > {shown}", timeout=20000)
            except Exception:
                break
        cards = _cards(self.page)
        return [c for c in cards if c.id not in seen], len(cards)

    def offer_text(self, offer_id: str) -> str | None:
        """The rendered text of one offer page, from the company name to the expiry date.
        None when the offer is gone (the site then shows "PAGE NON TROUVÉE")."""
        self._goto(f"{BASE}/offres/{offer_id}")
        try:
            self.page.wait_for_function(
                "() => /REFERENCE|PAGE NON TROUV/.test(document.body.innerText)", timeout=30000
            )
        except Exception:
            return None
        text = self.page.locator("main").inner_text() if self.page.locator("main").count() else self.page.inner_text("body")
        if "REFERENCE" not in text:
            return None
        end = text.find("VOUS ÊTES INTERESSÉ")
        return text[:end].strip() if end > 0 else text.strip()


def read_offers(ids: Iterable[str]) -> dict[str, str]:
    with Reader() as reader:
        return {i: reader.offer_text(i) for i in ids}
