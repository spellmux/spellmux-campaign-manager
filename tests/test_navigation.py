import re
from pathlib import Path

INDEX = Path(__file__).parents[1] / "src" / "campaign_manager" / "static" / "index.html"


def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_campaign_navigation_targets_discrete_pages() -> None:
    html = index_html()
    menu_targets = re.findall(r'data-campaign-page="([^"]+)"', html)

    assert menu_targets == [
        "overview",
        "sessions",
        "guide",
        "speakers",
        "review",
        "so-far",
        "settings",
    ]
    for target in menu_targets:
        assert f'id="campaign-{target}-page"' in html


def test_navigation_markup_has_unique_element_ids() -> None:
    ids = re.findall(r'\sid="([^"]+)"', index_html())

    assert len(ids) == len(set(ids))


def test_session_speaker_review_routes_to_campaign_speaker_management() -> None:
    html = index_html()

    assert 'id="campaign-speaker-form"' in html
    assert 'id="manage-campaign-speakers"' in html
    assert 'id="speaker-profile-form"' not in html
