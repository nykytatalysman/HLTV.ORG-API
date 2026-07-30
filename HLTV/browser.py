"""Lazy Selenium 4 browser management."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from .exceptions import HLTVBlockedError, HLTVNavigationError


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    html: str
    title: str = ""


def is_cloudflare_challenge(page: Page) -> bool:
    """Return whether a page is a recognizable Cloudflare interstitial."""
    title = page.title.strip().casefold()
    html = page.html.casefold()
    return title in {
        "just a moment...",
        "attention required! | cloudflare",
        "access denied",
    } or any(
        marker in html
        for marker in (
            "/cdn-cgi/challenge-platform/",
            'id="challenge-running"',
            'class="cf-chl-',
        )
    )


class SeleniumFetcher:
    """Load HLTV pages through a real browser.

    The driver starts only on the first request. A caller may inject an existing
    Selenium driver, which also makes integration with grids and containers easy.
    """

    def __init__(
        self,
        *,
        browser: str = "auto",
        headless: bool = False,
        timeout: float = 30,
        min_interval: float = 2.0,
        profile_dir: str | None = None,
        driver: Any | None = None,
    ) -> None:
        self.browser_name = browser.lower()
        self.headless = headless
        self.timeout = timeout
        self.min_interval = max(0.0, float(min_interval))
        self.profile_dir = (
            str(Path(profile_dir).expanduser().resolve()) if profile_dir else ""
        )
        self._driver = driver
        self._owns_driver = driver is None
        self._last_request_at = 0.0

    @property
    def driver(self) -> Any:
        if self._driver is None:
            self._driver = self._create_driver()
        return self._driver

    def _create_driver(self) -> Any:
        try:
            from selenium import webdriver
        except ImportError as exc:  # pragma: no cover - packaging prevents this
            raise HLTVNavigationError(
                "Selenium is not installed. Install the package with "
                "`pip install -e .`."
            ) from exc

        requested = self.browser_name
        choices = [requested] if requested != "auto" else ["chrome", "edge", "firefox"]
        failures: list[str] = []
        for choice in choices:
            try:
                if choice == "chrome":
                    options = webdriver.ChromeOptions()
                    if self.headless:
                        options.add_argument("--headless=new")
                    self._configure_chromium(options)
                    if self.profile_dir:
                        options.add_argument(f"--user-data-dir={self.profile_dir}")
                    driver = webdriver.Chrome(options=options)
                elif choice == "edge":
                    options = webdriver.EdgeOptions()
                    if self.headless:
                        options.add_argument("--headless=new")
                    self._configure_chromium(options)
                    if self.profile_dir:
                        options.add_argument(f"--user-data-dir={self.profile_dir}")
                    driver = webdriver.Edge(options=options)
                elif choice == "firefox":
                    options = webdriver.FirefoxOptions()
                    if self.headless:
                        options.add_argument("-headless")
                    if self.profile_dir:
                        options.profile = webdriver.FirefoxProfile(self.profile_dir)
                    driver = webdriver.Firefox(options=options)
                else:
                    raise ValueError(
                        "browser must be one of: auto, chrome, edge, firefox"
                    )
                driver.set_page_load_timeout(self.timeout)
                return driver
            except Exception as exc:  # try the next locally installed browser
                failures.append(f"{choice}: {exc}")

        detail = "; ".join(failures)
        raise HLTVNavigationError(
            "Could not start a supported browser. Install current Chrome, Edge, "
            f"or Firefox and retry. Driver errors: {detail}"
        )

    @staticmethod
    def _configure_chromium(options: Any) -> None:
        options.add_argument("--window-size=1440,1200")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.page_load_strategy = "eager"

    def _follow_existing_link(self, driver: Any, url: str) -> bool:
        """Use an on-page link for same-site navigation when one is present."""
        current = urlsplit(driver.current_url or "")
        target = urlsplit(url)
        if (
            current.hostname not in {"hltv.org", "www.hltv.org"}
            or target.hostname not in {"hltv.org", "www.hltv.org"}
        ):
            return False
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait

            wanted = urldefrag(url)[0].rstrip("/")
            link = next(
                (
                    item
                    for item in driver.find_elements(By.CSS_SELECTOR, "a[href]")
                    if urldefrag(
                        urljoin(driver.current_url, item.get_attribute("href") or "")
                    )[0].rstrip("/")
                    == wanted
                ),
                None,
            )
            if link is None:
                return False
            previous = driver.current_url
            driver.execute_script("arguments[0].click()", link)
            WebDriverWait(driver, self.timeout).until(
                lambda item: urldefrag(item.current_url)[0].rstrip("/") == wanted
                or item.current_url != previous
            )
            return True
        except Exception:
            return False

    def fetch(self, url: str) -> Page:
        try:
            driver = self.driver
            elapsed = monotonic() - self._last_request_at
            if self._last_request_at and elapsed < self.min_interval:
                sleep(self.min_interval - elapsed)
            if not self._follow_existing_link(driver, url):
                driver.get(url)
            self._last_request_at = monotonic()
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(driver, self.timeout).until(
                lambda item: item.execute_script("return document.readyState")
                in {"interactive", "complete"}
            )
            # With an eager page-load strategy Cloudflare can briefly expose an
            # interstitial title before the real document is ready.
            # Preserve a timed-out page so the block-page check below can raise
            # the more useful HLTVBlockedError.
            with suppress(TimeoutException):
                WebDriverWait(driver, self.timeout).until(
                    lambda item: "hltv.org" in (item.title or "").casefold()
                )
            page = Page(
                url=driver.current_url,
                html=driver.page_source,
                title=driver.title or "",
            )
        except HLTVNavigationError:
            raise
        except Exception as exc:
            raise HLTVNavigationError(f"Could not load {url}: {exc}") from exc

        if is_cloudflare_challenge(page):
            raise HLTVBlockedError(
                "HLTV blocked this browser session. Use visible mode and a dedicated "
                "`profile_dir`, complete any challenge in the browser window, "
                "then retry."
            )
        return page

    def close(self) -> None:
        if self._driver is not None and self._owns_driver:
            try:
                self._driver.quit()
            finally:
                self._driver = None

    def __enter__(self) -> SeleniumFetcher:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
