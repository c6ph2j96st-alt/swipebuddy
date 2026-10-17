"""Tinder-specific DOM knowledge. This is the ONLY file that should know it.

Everything here is best-effort: Tinder uses obfuscated, frequently changing
class names and may change its markup, text or URLs at any time. Each
``*_SELECTORS`` / ``*_MARKERS`` constant is a tuple of candidates that are tried
in order; update this file (and nothing else) when detection stops working.
Text markers are matched case-insensitively against the page text.
"""
from __future__ import annotations

TINDER_URL = "https://tinder.com/app/recs"
TINDER_HOST = "tinder.com"
APP_PATH_PREFIX = "/app"

# Active profile card container (first match wins).
CARD_SELECTORS: tuple[str, ...] = (
    '[class*="recsCardboard"] [class*="Bdrs"]',
    'div[aria-label*="Profile"]',
    'main [class*="recCard"]',
    '[class*="recsCardboard"]',
    'main [role="img"]',
)

# Visible photo inside the card (diagnostics / future cropping).
PHOTO_SELECTORS: tuple[str, ...] = (
    '[class*="recsCardboard"] [style*="background-image"]',
    'div[aria-label*="Profile"] [style*="background-image"]',
    'main [class*="recCard"] img',
    "main img[src]",
)

# Elements indicating the user is not logged in (besides the URL check).
LOGGED_OUT_MARKERS: tuple[str, ...] = (
    'a:has-text("Log in")',
    'button:has-text("Log in")',
    'a:has-text("Logga in")',
    'button:has-text("Logga in")',
    '[data-testid="login"]',
)

# Page text shown when the deck is empty (lower-case).
NO_PROFILES_MARKERS: tuple[str, ...] = (
    "there's no one new around you",
    "there’s no one new around you",
    "no one new around you",
    "out of potential matches",
    "det finns ingen ny i närheten",
    "inga fler potentiella matchningar",
    "slut på potentiella matchningar",
)

# CAPTCHA / verification: iframe elements plus visible text (lower-case).
BLOCKED_SELECTORS: tuple[str, ...] = (
    'iframe[src*="arkoselabs"]',
    'iframe[src*="funcaptcha"]',
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="captcha"]',
)
BLOCKED_TEXT_MARKERS: tuple[str, ...] = (
    "verify you are human",
    "verify you're human",
    "verify your identity",
    "verifiera att du är en människa",
    "verifiera din identitet",
)

# Dialogs: matches, upsells, rate prompts.
MODAL_SELECTORS: tuple[str, ...] = ('[role="dialog"]',)
MODAL_TEXT_MARKERS: tuple[str, ...] = (
    "it's a match",
    "it’s a match",
    "det är en match",
)

KEY_LIKE = "ArrowRight"
KEY_PASS = "ArrowLeft"
KEY_NEXT_PHOTO = "Space"
KEY_DISMISS = "Escape"
