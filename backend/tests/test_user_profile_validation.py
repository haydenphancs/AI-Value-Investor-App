"""`PATCH /users/me` — the display name is written to a column nothing bounded.

`users.display_name` is a bare `text` column with no CHECK. `update_profile` did
`request.model_dump(exclude_none=True)` straight into the UPDATE, the Pydantic model declared
`Optional[str]` with no length, and the request-body cap middleware covered only `/me/settings`.

So any holder of a valid token could store a multi-megabyte display name — which
`get_current_user` then re-reads on EVERY authenticated request (it does `select("*")`) and
which is echoed into the profile response, the PDF byline and the support report. One write,
a permanent cost on every subsequent request by that user.

`exclude_none=True` only drops `None`, so an empty or whitespace-only string was a real value
and blanked the name; iOS falls back to "Investor" on `nil` only, so the row rendered empty.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import SignUpRequest
from app.schemas.user import (
    AVATAR_URL_MAX_LENGTH,
    DISPLAY_NAME_MAX_LENGTH,
    UpdateProfileRequest,
)


def test_a_reasonable_name_is_accepted():
    assert UpdateProfileRequest(display_name="Hai Phan").display_name == "Hai Phan"


def test_an_oversized_display_name_is_rejected():
    with pytest.raises(ValidationError):
        UpdateProfileRequest(display_name="A" * (DISPLAY_NAME_MAX_LENGTH + 1))


def test_a_name_at_the_limit_is_still_accepted():
    """Boundary, so the bound cannot drift to off-by-one and start rejecting real names."""
    name = "A" * DISPLAY_NAME_MAX_LENGTH
    assert UpdateProfileRequest(display_name=name).display_name == name


def test_an_empty_display_name_is_rejected():
    """`exclude_none=True` does not drop "", so this would have blanked the row."""
    with pytest.raises(ValidationError):
        UpdateProfileRequest(display_name="")


def test_avatar_url_is_not_client_writable():
    """`avatar_url` used to be a bounded field here. It is now SERVER-OWNED.

    Bounding its length was never the real protection. The column feeds a signed-URL minter
    that runs on the service-role key, so a caller who could write it could aim their own
    avatar at an arbitrary object — including one inside our own private Storage bucket — and
    have the read path sign it for them. Removing the field is what makes
    `avatar_service._own_object_path`'s per-user prefix rule an invariant rather than a hope.

    `model_config` is Pydantic's default (ignore extras), so a client still sending the field
    is IGNORED rather than rejected — which is what keeps an older build's PATCH working.
    """
    assert "avatar_url" not in UpdateProfileRequest.model_fields

    req = UpdateProfileRequest(avatar_url="javascript:alert(1)", display_name="Hai")
    assert req.model_dump(exclude_none=True) == {"display_name": "Hai"}


def test_the_avatar_url_bound_still_guards_the_constructed_url():
    """The constant kept its job when the field lost its. `store_avatar` asserts the URL it
    BUILDS against it, so a storage-host change that blows past 2048 fails loudly instead of
    truncating into a column with no CHECK."""
    import inspect

    from app.services import avatar_service

    src = inspect.getsource(avatar_service.store_avatar)
    assert "AVATAR_URL_MAX_LENGTH" in src, (
        "nothing reads AVATAR_URL_MAX_LENGTH any more — either use it or delete it, but do "
        "not leave a constant that looks like a guard and is not one"
    )


def test_the_upload_field_is_deliberately_unbounded():
    """A `max_length` on `image_base64` would be enforced by Pydantic BEFORE the handler, so
    an oversize photo would surface as a 422 carrying the validator's own text instead of
    AVATAR_TOO_LARGE — making the typed code unreachable for the exact input it exists for.
    The size decision belongs to `decode_and_validate`."""
    from app.schemas.user import AVATAR_MAX_BYTES, UpdateAvatarRequest

    field = UpdateAvatarRequest.model_fields["image_base64"]
    bounds = [getattr(m, "max_length", None) for m in field.metadata]
    assert not any(b is not None for b in bounds), (
        f"image_base64 grew a max_length ({bounds}) — that preempts AVATAR_TOO_LARGE"
    )
    # And an over-cap payload must reach the service and raise the TYPED error.
    import base64

    from app.services.avatar_service import AvatarTooLargeError, decode_and_validate

    oversize = b"\xff\xd8\xff" + b"x" * AVATAR_MAX_BYTES
    with pytest.raises(AvatarTooLargeError):
        decode_and_validate(base64.b64encode(oversize).decode())


def test_omitting_both_fields_is_still_valid_at_the_schema():
    """The endpoint, not the schema, decides that an empty patch is a 400 — the schema must
    keep both optional or a caller could not update just one of them."""
    req = UpdateProfileRequest()
    assert req.model_dump(exclude_none=True) == {}


def test_signup_applies_the_same_bound_to_the_same_column():
    """UNAUTHENTICATED route, SAME column. Bounding only the authenticated one is pointless."""
    with pytest.raises(ValidationError):
        SignUpRequest(
            email="a@b.com",
            password="correct horse battery",
            display_name="A" * (DISPLAY_NAME_MAX_LENGTH + 1),
        )


def test_signup_and_patch_share_one_bound():
    """Two independent literals would drift, and the unauthenticated one would be the loose
    one — which is the wrong direction to be wrong in."""
    from app.schemas import auth as auth_schemas

    assert auth_schemas.DISPLAY_NAME_MAX_LENGTH is DISPLAY_NAME_MAX_LENGTH


def test_the_body_cap_middleware_covers_the_profile_route():
    """The Pydantic bound is the real guard; this is the cheap outer one that rejects an
    oversized body before it is parsed at all."""
    from app.main import _BODY_CAPPED_PATH_SUFFIXES

    assert "/api/v1/users/me".endswith(_BODY_CAPPED_PATH_SUFFIXES), (
        f"PATCH /users/me is not body-capped; suffixes are {_BODY_CAPPED_PATH_SUFFIXES}"
    )
    # The pre-existing entry must survive — this list is easy to overwrite rather than extend.
    assert "/api/v1/users/me/settings".endswith(_BODY_CAPPED_PATH_SUFFIXES)

    # The avatar route needs its OWN entry: `endswith` does not walk up a path, so
    # "/api/v1/users/me/avatar".endswith("/users/me") is False and it would inherit no cap
    # at all — on the one route in the app that carries hundreds of KB.
    assert "/api/v1/users/me/avatar".endswith(_BODY_CAPPED_PATH_SUFFIXES), (
        f"the avatar upload is not body-capped; suffixes are {_BODY_CAPPED_PATH_SUFFIXES}"
    )
