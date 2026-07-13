"""Unit tests for VPS OCR captcha client (mocked HTTP)."""

from unittest.mock import MagicMock, patch

import pytest

from captcha_ocr import (
    normalize_captcha_text,
    parse_ocr_response,
    predict_file,
    solve_captcha_bytes_with_ocr,
    validate_captcha_text,
)


def test_normalize_captcha_text():
    assert normalize_captcha_text(" ab-12X ") == "AB12X"


def test_parse_ocr_response():
    assert parse_ocr_response({"result": "Hello"}) == "Hello"


def test_parse_ocr_response_missing_key():
    with pytest.raises(ValueError, match="missing 'result'"):
        parse_ocr_response({})


def test_validate_captcha_text_too_short():
    with pytest.raises(ValueError, match="suspicious"):
        validate_captcha_text("ab", source="OCR")


@patch("captcha_ocr.requests.post")
def test_predict_file_multipart(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": "ABCD1"}
    mock_post.return_value = mock_resp

    out = predict_file("https://ocr.example", b"\x89PNG", filename="captcha.png")
    assert out["result"] == "ABCD1"

    mock_post.assert_called_once()
    call_kw = mock_post.call_args.kwargs
    assert call_kw["headers"]["ngrok-skip-browser-warning"] == "1"
    files = call_kw["files"]
    assert files["file"][0] == "captcha.png"
    assert files["file"][2] == "image/png"
    assert call_kw["timeout"] == 30 or isinstance(call_kw["timeout"], int)


@patch("captcha_ocr.predict_file")
def test_solve_captcha_bytes_with_ocr(mock_predict: MagicMock) -> None:
    mock_predict.return_value = {"result": "WXYZ9"}
    assert solve_captcha_bytes_with_ocr(b"png", base_url="https://ocr.example") == "WXYZ9"


@patch("booker_legacy.solve_captcha_with_ocr")
@patch("booker_legacy.solve_captcha_with_gemini")
def test_solve_captcha_ocr_fallback_to_gemini(
    mock_gemini: MagicMock, mock_ocr: MagicMock
) -> None:
    import booker_legacy as app

    mock_ocr.side_effect = ValueError("ocr down")
    mock_gemini.return_value = "GEMINI1"

    with patch.dict("os.environ", {"CAPTCHA_SOLVER": "ocr_with_gemini_fallback"}, clear=False):
        out = app.solve_captcha(b"x", run_id="t", attempt=1, vcid="v")
    assert out == "GEMINI1"
    mock_gemini.assert_called_once()


@patch("booker_legacy.solve_captcha_with_gemini")
def test_solve_captcha_gemini_only(mock_gemini: MagicMock) -> None:
    import booker_legacy as app

    mock_gemini.return_value = "ONLY1"
    with patch.dict("os.environ", {"CAPTCHA_SOLVER": "gemini"}, clear=False):
        out = app.solve_captcha(b"x")
    assert out == "ONLY1"
