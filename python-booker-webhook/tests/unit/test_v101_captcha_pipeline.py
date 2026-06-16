"""Unit tests for v1.0.1 OCR/captcha pipeline merge (HS guard, DOM-only fetch, OCR filename)."""

import pytest
from unittest.mock import MagicMock, patch

import booker_legacy as booker
from booker_legacy import (
    DomCaptchaCaptureError,
    captcha_hs_stale_after_fill,
    captcha_parallel_fetch_enabled,
    fill_captcha_with_hs_guard,
    solve_captcha_with_ocr,
)


def test_captcha_parallel_fetch_always_disabled() -> None:
    """v1.0.1 hard-disables parallel URL prefetch for solving."""
    with patch.dict("os.environ", {"CAPTCHA_PARALLEL_FETCH": "true"}, clear=False):
        assert captcha_parallel_fetch_enabled() is False


def test_captcha_hs_stale_empty_typed() -> None:
    assert captcha_hs_stale_after_fill("abc", "def", "") is True


def test_captcha_hs_stale_unchanged_hs() -> None:
    assert captcha_hs_stale_after_fill("same1234", "same1234", "ABCD", "") is True


def test_captcha_hs_stale_dom_match_ready() -> None:
    assert captcha_hs_stale_after_fill("before", "after", "ABCD", "abcd") is False


def test_captcha_hs_stale_empty_hs_after() -> None:
    assert captcha_hs_stale_after_fill("before", "", "ABCD", "") is True


@patch("booker_legacy._fill_captcha_text_like_user")
@patch("booker_legacy.wait_botdetect_ready")
@patch("booker_legacy._read_bdc_hs")
def test_fill_captcha_with_hs_guard_success(
    mock_read_hs: MagicMock,
    mock_ready: MagicMock,
    mock_fill: MagicMock,
) -> None:
    page = MagicMock()
    mock_ready.return_value = True
    mock_read_hs.side_effect = ["before", "after"]
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.first.input_value.return_value = "ABCD"

    assert fill_captcha_with_hs_guard(page, "ABCD", run_id="r1", settle_ms=0) is True
    mock_fill.assert_called_once()


@patch("booker_legacy._fill_captcha_text_like_user")
@patch("booker_legacy.wait_botdetect_ready")
@patch("booker_legacy._read_bdc_hs")
def test_fill_captcha_with_hs_guard_stale_returns_false(
    mock_read_hs: MagicMock,
    mock_ready: MagicMock,
    mock_fill: MagicMock,
) -> None:
    page = MagicMock()
    mock_ready.return_value = True
    mock_read_hs.return_value = "stale123"
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.first.input_value.return_value = "WRONG"

    assert fill_captcha_with_hs_guard(page, "ABCD", run_id="r2", settle_ms=0) is False
    assert mock_fill.call_count == 3


@patch("booker_legacy.ocr_predict_file")
@patch("booker_legacy.captcha_ocr_base_url")
def test_solve_captcha_with_ocr_unique_filename(
    mock_base_url: MagicMock,
    mock_predict: MagicMock,
) -> None:
    mock_base_url.return_value = "http://127.0.0.1:8001"
    mock_predict.return_value = {"result": "ABCD1"}

    text = solve_captcha_with_ocr(
        b"fake-png-bytes",
        run_id="run-test",
        attempt=2,
        vcid="vcid12345678",
    )

    assert text == "ABCD1"
    mock_predict.assert_called_once()
    filename = mock_predict.call_args.kwargs["filename"]
    assert filename.startswith("captcha_run-test_02_")
    assert "vcid12345678" in filename
    assert filename.endswith(".png")
    assert filename != "captcha.png"


@patch("booker_legacy.capture_captcha_bytes_from_dom")
@patch("booker_legacy.wait_botdetect_ready")
def test_fetch_captcha_dom_only_no_parallel(
    mock_ready: MagicMock,
    mock_dom: MagicMock,
) -> None:
    mock_ready.return_value = True
    mock_dom.return_value = b"dom-bytes"
    page = MagicMock()
    page.locator.return_value.count.return_value = 1
    page.locator.return_value.first.screenshot.return_value = b"site-png"
    fresh = booker.FreshFields(
        booking_endpoint="",
        token="",
        start_time="",
        bdc_vcid="v1",
        bdc_hs="hs",
        bdc_sp="sp",
        bdc_bw="",
        captcha_image_url="http://example/captcha",
        html="",
    )

    captcha_bytes, site_png, source, timings = booker.fetch_captcha_with_parallel_prefetch(
        page=page,
        context=MagicMock(),
        fresh=fresh,
        session_cookie="s=1",
        settle_ms=0,
        run_id="r3",
        attempt=1,
    )

    assert captcha_bytes == b"dom-bytes"
    assert source == "dom_screenshot"
    assert "captchaParallelFetchMs" not in timings


@patch("booker_legacy.fetch_captcha_bytes")
@patch("booker_legacy.capture_captcha_bytes_from_dom")
@patch("booker_legacy.wait_botdetect_ready")
def test_fetch_captcha_dom_exhausted_raises_no_url_fallback(
    mock_ready: MagicMock,
    mock_dom: MagicMock,
    mock_url_fetch: MagicMock,
) -> None:
    mock_ready.return_value = True
    mock_dom.side_effect = RuntimeError("screenshot failed")
    page = MagicMock()
    page.locator.return_value.count.return_value = 0
    fresh = booker.FreshFields(
        booking_endpoint="",
        token="",
        start_time="",
        bdc_vcid="v1",
        bdc_hs="hs",
        bdc_sp="sp",
        bdc_bw="",
        captcha_image_url="http://example/captcha",
        html="",
    )

    with pytest.raises(DomCaptchaCaptureError, match="DOM captcha capture failed"):
        booker.fetch_captcha_with_parallel_prefetch(
            page=page,
            context=MagicMock(),
            fresh=fresh,
            session_cookie="s=1",
            settle_ms=0,
            run_id="r4",
            attempt=1,
        )

    mock_url_fetch.assert_not_called()
    assert mock_dom.call_count == 3
