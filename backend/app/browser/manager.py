"""Persistent browser context manager using Playwright.

Launches a browser with a persistent user data directory so that
cookies, localStorage, and cache survive across app restarts.
"""

import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages a persistent Playwright browser context.

    Uses launch_persistent_context so that browser state (cookies,
    localStorage, cache) persists to disk like a real Chrome profile.

    Usage:
        # Start (called from app lifespan)
        await browser_manager.start()

        # Access the active page
        page = browser_manager.page

        # Stop (called from app lifespan)
        await browser_manager.stop()
    """

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None

    @property
    def user_data_dir(self) -> Path:
        """Resolve the browser user data directory.

        Path is relative to the project root (parent of backend/).
        """
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        return project_root / settings.BROWSER_USER_DATA_DIR

    @property
    def context(self):
        """Return the active browser context.

        Raises:
            RuntimeError: If browser has not been started.
        """
        if self._context is None:
            raise RuntimeError("Browser not started. Call browser_manager.start() first.")
        return self._context

    @property
    def page(self):
        """Return the default browser page.

        Raises:
            RuntimeError: If browser has not been started.
        """
        if self._page is None:
            raise RuntimeError("Browser not started. Call browser_manager.start() first.")
        return self._page

    @property
    def is_running(self) -> bool:
        """Check if the browser context is active."""
        return self._context is not None

    async def start(self) -> None:
        """Launch the persistent browser context.

        Should be called during app startup (lifespan).
        """
        if not settings.BROWSER_ENABLED:
            logger.info("Browser automation is disabled (BROWSER_ENABLED=false)")
            return

        from playwright.async_api import async_playwright

        user_data_dir = self.user_data_dir
        user_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Starting persistent browser context "
            f"(headless={settings.BROWSER_HEADLESS}, "
            f"channel={settings.BROWSER_CHANNEL}, "
            f"user_data_dir={user_data_dir})"
        )

        self._playwright = await async_playwright().start()

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=settings.BROWSER_HEADLESS,
            channel=settings.BROWSER_CHANNEL,
        )

        # Get or create the default page
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        logger.info("Persistent browser context started successfully")

    async def stop(self) -> None:
        """Close the browser context and stop Playwright.

        Should be called during app shutdown (lifespan).
        """
        if not settings.BROWSER_ENABLED:
            return

        if self._context:
            logger.info("Closing persistent browser context")
            await self._context.close()
            self._context = None
            self._page = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Browser stopped")


# Singleton instance
browser_manager = BrowserManager()
