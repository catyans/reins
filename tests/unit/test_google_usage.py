from decimal import Decimal

import pytest

from reins.google_usage import GoogleTextPrices, IncompleteGoogleUsage, estimate_google_text_cost


def test_cached_input_and_reasoning_not_double_counted():
    rates = GoogleTextPrices(Decimal(".1"), Decimal(".4"), Decimal(".01"))
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 600},
        "completion_tokens_details": {"reasoning_tokens": 20},
    }
    assert estimate_google_text_cost(usage, rates) == Decimal(".000086")


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 10},
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 2}},
        {"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"audio_tokens": 1}},
    ],
)
def test_unpriced_usage_never_free(usage):
    with pytest.raises(IncompleteGoogleUsage):
        estimate_google_text_cost(
            usage, GoogleTextPrices(Decimal(".1"), Decimal(".4"), Decimal(".01"))
        )
