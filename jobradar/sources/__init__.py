"""Registry of job sources. Future Playwright adapters (Naukri etc.) slot in here."""

from .adzuna import AdzunaAdapter
from .base import SourceAdapter
from .linkedin import LinkedInAdapter
from .naukri import NaukriAdapter

SOURCES = {
    LinkedInAdapter.name: LinkedInAdapter,
    AdzunaAdapter.name: AdzunaAdapter,
    NaukriAdapter.name: NaukriAdapter,
}
