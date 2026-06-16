"""Turbo captcha solver mode matrix (_solve_turbo_captcha in app.py)."""

from unittest.mock import MagicMock, patch

from app import _solve_turbo_captcha


def _env_solver(**overrides: str):
    """Patch solver env; TURBO_CAPTCHA_SOLVER wins over CAPTCHA_SOLVER in app.py."""
    base = {"TURBO_CAPTCHA_SOLVER": "", "CAPTCHA_SOLVER": "ocr_with_gemini_fallback"}
    base.update(overrides)
    return patch.dict("os.environ", base, clear=False)


def _solve(**kwargs):
    defaults = {
        "run_id": "r1",
        "attempt": 1,
        "captcha_round": 1,
        "vcid": "vcid123",
        "max_attempts": 3,
    }
    defaults.update(kwargs)
    return _solve_turbo_captcha(b"png", **defaults)


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_ocr_with_gemini_fallback_uses_gemini_only_when_ocr_raises(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    mock_ocr.side_effect = ValueError("OCR service down")
    mock_gemini.return_value = "GEMINI1"

    with _env_solver(CAPTCHA_SOLVER="ocr_with_gemini_fallback"):
        text, source = _solve(attempt=1, captcha_round=1)

    assert text == "GEMINI1"
    assert source == "gemini_fallback"
    mock_gemini.assert_called_once()
    mock_ocr.assert_called_once()


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_ocr_round_1_wrong_answer_does_not_call_gemini(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    """Round 1: wrong OCR answer still returns without Gemini (v1.0.1 behavior)."""
    mock_ocr.return_value = "WRONG1"

    with _env_solver(CAPTCHA_SOLVER="ocr_with_gemini_fallback"):
        text, source = _solve(run_id="r2", attempt=1, captcha_round=1, vcid="vcid456")

    assert text == "WRONG1"
    assert source == "ocr"
    mock_ocr.assert_called_once()
    mock_gemini.assert_not_called()


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_gemini_first_on_captcha_round_2(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    mock_gemini.return_value = "GEMINI2"

    with _env_solver(CAPTCHA_SOLVER="ocr_with_gemini_fallback"):
        text, source = _solve(run_id="r2b", attempt=1, captcha_round=2, vcid="vcid456")

    assert text == "GEMINI2"
    assert source == "gemini"
    mock_gemini.assert_called_once()
    mock_ocr.assert_not_called()


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_ocr_first_on_captcha_round_1(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    mock_ocr.return_value = "OCR123"

    with _env_solver(CAPTCHA_SOLVER="ocr_with_gemini_fallback"):
        text, source = _solve(run_id="r2c", attempt=1, captcha_round=1, vcid="vcid456")

    assert text == "OCR123"
    assert source == "ocr"
    mock_ocr.assert_called_once()
    mock_gemini.assert_not_called()


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_gemini_first_on_attempt_2_even_at_round_1(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    mock_gemini.return_value = "GEMINI3"

    with _env_solver(CAPTCHA_SOLVER="ocr_with_gemini_fallback"):
        text, source = _solve(run_id="r2d", attempt=2, captcha_round=1, vcid="vcid456")

    assert text == "GEMINI3"
    assert source == "gemini"
    mock_gemini.assert_called_once()
    mock_ocr.assert_not_called()


@patch("booker_legacy.solve_captcha_with_gemini")
@patch("booker_legacy.solve_captcha_with_ocr")
def test_gemini_first_mode_uses_gemini_without_calling_ocr(
    mock_ocr: MagicMock, mock_gemini: MagicMock
) -> None:
    mock_gemini.return_value = "GEMINI2"

    with _env_solver(CAPTCHA_SOLVER="gemini_first"):
        text, source = _solve(run_id="r3", attempt=1, captcha_round=1, vcid="vcid789")

    assert text == "GEMINI2"
    assert source == "gemini"
    mock_gemini.assert_called_once()
    mock_ocr.assert_not_called()


@patch("booker_legacy.solve_captcha_with_gemini")
@patch("booker_legacy.solve_captcha")
def test_gemini_first_falls_back_to_solve_captcha_when_gemini_raises(
    mock_solve: MagicMock, mock_gemini: MagicMock
) -> None:
    mock_gemini.side_effect = ValueError("Gemini API error")
    mock_solve.return_value = "OCRFB1"

    with _env_solver(CAPTCHA_SOLVER="gemini_first"):
        text, source = _solve(run_id="r4", attempt=2, captcha_round=2, vcid="vcid000", max_attempts=5)

    assert text == "OCRFB1"
    assert source == "ocr_fallback"
    mock_gemini.assert_called_once()
    mock_solve.assert_called_once()


@patch("booker_legacy.solve_captcha_with_ocr")
def test_turbo_captcha_solver_env_overrides_captcha_solver(mock_ocr: MagicMock) -> None:
    mock_ocr.return_value = "OCRONLY"

    with _env_solver(CAPTCHA_SOLVER="gemini_first", TURBO_CAPTCHA_SOLVER="ocr"):
        text, source = _solve(run_id="r5", attempt=1, captcha_round=1, vcid="v", max_attempts=1)

    assert text == "OCRONLY"
    assert source == "ocr"
