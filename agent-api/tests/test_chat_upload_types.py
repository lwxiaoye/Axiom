from app.services.files.chat_upload_types import chat_upload_reject_reason


def test_chat_upload_reject_reason_allows_bidding_formats():
    assert chat_upload_reject_reason("a.pdf") == ""
    assert chat_upload_reject_reason("a.docx") == ""
    assert chat_upload_reject_reason("a.html") == ""
    assert chat_upload_reject_reason("a.csv") == ""
    assert chat_upload_reject_reason("shot.png") == ""
    assert chat_upload_reject_reason("paste", "image/jpeg") == ""


def test_chat_upload_reject_reason_blocks_zip_and_legacy_office():
    assert "不支持「payload.zip」" in chat_upload_reject_reason("payload.zip")
    assert "另存为 .pptx" in chat_upload_reject_reason("deck.ppt")
