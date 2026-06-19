"""
Schema-parity test for book bookmarks (backend <-> iOS).

Pins the shape iOS `BookmarkListResponse` decodes (`{"bookmarks": [String]}`). A drift here
would crash the iOS decoder in production, so this is a guard rail, not a nicety.
"""

from app.schemas.bookmarks import BookmarkListResponse, BookmarkRequest


def test_bookmark_list_response_shape():
    resp = BookmarkListResponse(bookmarks=["Rich Dad Poor Dad", "The Intelligent Investor"])
    dumped = resp.model_dump()
    # Exactly the one key the iOS decoder expects — no silent renames / extra fields.
    assert set(dumped.keys()) == {"bookmarks"}
    assert dumped["bookmarks"] == ["Rich Dad Poor Dad", "The Intelligent Investor"]


def test_bookmark_list_response_empty():
    # The endpoint degrades to an empty list on any backend hiccup; iOS must decode that too.
    assert BookmarkListResponse(bookmarks=[]).model_dump() == {"bookmarks": []}


def test_bookmark_list_response_validates_backend_dict():
    validated = BookmarkListResponse.model_validate({"bookmarks": ["X"]})
    assert validated.bookmarks == ["X"]


def test_bookmark_request_shape():
    # iOS sends {"book_key": "..."} on add/remove.
    req = BookmarkRequest.model_validate({"book_key": "The Psychology of Money"})
    assert req.book_key == "The Psychology of Money"
