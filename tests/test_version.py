import pytest

from version import guess_next_version


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("1.2.3", "1.3.3"),
        ("0.0", "0.1.0"),
        ("2", "2.1.0"),
    ],
)
def test_guess_next_version_normalizes_missing_components(
    current: str, expected: str
) -> None:
    assert guess_next_version(current) == expected


def test_guess_next_version_rejects_too_many_components() -> None:
    with pytest.raises(ValueError, match="invalid version number"):
        guess_next_version("1.2.3.4")
