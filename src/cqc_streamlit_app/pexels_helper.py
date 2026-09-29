# Import API class from pexels_api package
import os
import random

from pexels_api import API
from pexels_api.tools import Photo

_api: API | None = None


def _get_api() -> API:
    """Create the Pexels client on first use, so importing this module never fails."""
    global _api
    if _api is None:
        api_key = os.getenv('PEXELS_API_KEY')
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY is not set")
        _api = API(api_key)
    return _api


def get_photo(query: str) -> Photo:
    photos = get_photos(query)
    # Select 1 random photo from entries
    selected_photo = random.choice(photos)
    return selected_photo


def get_photos(query: str, num_of_photos: int = 25) -> list[Photo]:
    # Search for photos
    api = _get_api()
    api.search(query, page=1, results_per_page=num_of_photos)
    # Get photo entries
    photos = api.get_entries()

    return photos
