"""`app_store.notification_subscription_status`: Apple's `data.status` on a notification.

Every App Store Server Notification about an auto-renewable subscription carries Apple's own
status for it, as of the notification's signedDate (1 active, 2 expired, 3 billing retry,
4 billing grace period, 5 revoked). It sits inside the outer JWS that `verify_notification`
already verified. The billing-grace hold in iap_service only ever uses it to END a hold (2, 3
or 5), so a misread must come back as None, never as a status.

Asserted against the REAL library models as well as dicts: `_to_dict` flattens only the top
level, so in production `notification["data"]` is the library's attrs `Data` object. A suite
that fed only dicts once passed while every real purchase was rejected
(test_iap_payload_and_binding.py), which is why the real class is used here — and, below, the
library's own decoding step.
"""
from __future__ import annotations

import logging

import pytest
from appstoreserverlibrary.models.Data import Data
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2
from appstoreserverlibrary.models.ResponseBodyV2DecodedPayload import (
    ResponseBodyV2DecodedPayload,
)
from appstoreserverlibrary.models.Status import Status
from appstoreserverlibrary.models.Subtype import Subtype

from app.integrations import app_store


def _status(data):
    return app_store.notification_subscription_status({"notificationType": "X", "data": data})


@pytest.mark.parametrize("status", list(Status))
def test_every_apple_status_reads_from_the_real_data_model(status):
    assert _status(Data(status=status)) == int(status)


def test_the_shape_verify_notification_returns_is_read():
    """`verify_notification` returns `_to_dict(decoded)`: the top level flattened, `data` left as
    the attrs object. The status must survive exactly that path."""
    decoded = ResponseBodyV2DecodedPayload(
        notificationType=NotificationTypeV2.DID_CHANGE_RENEWAL_STATUS,
        subtype=Subtype.AUTO_RENEW_DISABLED,
        data=Data(status=Status.BILLING_GRACE_PERIOD),
    )
    notification = app_store._to_dict(decoded)
    assert notification["notificationType"] == "DID_CHANGE_RENEWAL_STATUS"
    assert isinstance(notification["data"], Data)
    assert app_store.notification_subscription_status(notification) == 4


@pytest.mark.parametrize("data, expected", [
    ({"status": 2}, 2),
    ({"status": Status.BILLING_RETRY}, 3),
    ({"rawStatus": 5}, 5),
    ({"rawStatus": None, "status": 4}, 4),
    ({"status": 1, "signedTransactionInfo": "jws"}, 1),
])
def test_a_dict_data_is_read_too(data, expected):
    assert _status(data) == expected


@pytest.mark.parametrize("notification", [
    {}, {"notificationType": "TEST"}, {"data": None}, {"data": {}},
    {"data": {"signedTransactionInfo": "jws"}}, {"data": Data()},
])
def test_no_status_is_none(notification):
    assert app_store.notification_subscription_status(notification) is None


def test_a_missing_notification_is_none():
    assert app_store.notification_subscription_status(None) is None


@pytest.mark.parametrize("value", [True, False, "4", "two", 4.0, 2.5, [4], {"v": 4}])
def test_a_non_integer_status_is_none_and_logged(value, caplog):
    with caplog.at_level(logging.WARNING, logger="app.integrations.app_store"):
        assert _status({"status": value}) is None
    assert any("non-integer data.status" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("value", [0, 6, -1, 99])
def test_an_unknown_status_is_none_and_logged(value, caplog):
    with caplog.at_level(logging.WARNING, logger="app.integrations.app_store"):
        assert _status({"status": value}) is None
        assert _status(Data(rawStatus=value)) is None
    assert any(f"unknown data.status {value}" in r.getMessage() for r in caplog.records)


def test_an_object_without_the_attributes_is_none():
    class _Bare:
        pass

    assert _status(_Bare()) is None


def _decoded_like_production(status):
    """The verified path: the library's cattrs converter structures the JSON payload (as
    `SignedDataVerifier.verify_and_decode_notification` does after the signature check), then
    `_to_dict` flattens its top level (as `verify_notification` does)."""
    from appstoreserverlibrary.models.LibraryUtility import _get_cattrs_converter

    payload = {"notificationType": "PRICE_INCREASE", "subtype": "ACCEPTED",
               "notificationUUID": "uuid-1", "data": {"status": status}}
    decoded = _get_cattrs_converter(ResponseBodyV2DecodedPayload).structure(
        payload, ResponseBodyV2DecodedPayload)
    return app_store._to_dict(decoded)


@pytest.mark.parametrize("raw, expected", [
    (4, 4), (2, 2), ("3", 3), (3.9, 3), (True, 1), (9, None), (0, None),
])
def test_the_status_read_through_the_librarys_own_decoding(raw, expected):
    """The converter applies `int()` before this parser ever sees the value, so the type
    strictness only guards dicts and hand-built objects: what reaches it here is already an int
    (Apple signs it). An unknown int still comes back as None."""
    assert app_store.notification_subscription_status(_decoded_like_production(raw)) == expected


def test_a_non_numeric_status_fails_the_librarys_decoding():
    with pytest.raises(Exception):
        _decoded_like_production("x")


def test_the_warning_names_the_notification(caplog):
    with caplog.at_level(logging.WARNING, logger="app.integrations.app_store"):
        assert app_store.notification_subscription_status(
            {"notificationType": "DID_RENEW", "notificationUUID": "uuid-9",
             "data": {"status": 7}}) is None
    msgs = [r.getMessage() for r in caplog.records]
    assert any("DID_RENEW" in m and "uuid=uuid-9" in m for m in msgs)
