"""Post-submit HTML classification (already booked, slot taken)."""

from booker_legacy import classify_post_submit_html, extract_message_error_texts

_ALREADY_BOOKED_HTML = (
    '<p class="message-error">You have already reserved an appointment.</p>'
)


def test_extract_message_error_from_html():
    texts = extract_message_error_texts(_ALREADY_BOOKED_HTML)
    assert texts == ["You have already reserved an appointment."]


def test_classify_already_booked():
    assert classify_post_submit_html(_ALREADY_BOOKED_HTML) == (
        "ALREADY_BOOKED",
        "You have already reserved an appointment.",
    )


def test_classify_slot_taken_allocated():
    html = (
        '<p class="message-error">The appointment was unfortunately '
        "allocated otherwise in the meantime.</p>"
    )
    assert classify_post_submit_html(html) == (
        "SLOT_TAKEN",
        "The appointment was unfortunately allocated otherwise in the meantime.",
    )


def test_classify_slot_taken_no_appointments():
    html = '<p class="message-error">No appointments available.</p>'
    assert classify_post_submit_html(html) == (
        "SLOT_TAKEN",
        "No appointments available.",
    )
