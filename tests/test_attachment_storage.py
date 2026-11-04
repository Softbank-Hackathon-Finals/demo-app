import io
import uuid

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import AttachmentConflict, AttachmentStorage, AttachmentUnavailable
from tests.test_api import FakeStore


@pytest.fixture
def storage():
    result = object.__new__(AttachmentStorage)
    result.bucket = "synthetic-attachments"
    result.client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test-only",
        aws_secret_access_key="test-only",
        endpoint_url="https://unused.invalid",
        config=Config(retries={"total_max_attempts": 1}),
    )
    with Stubber(result.client) as stub:
        yield result, stub
        stub.assert_no_pending_responses()


def put_params(storage, key, data=b"original", content_type="text/plain"):
    return dict(
        Bucket=storage.bucket, Key=key, Body=data, ContentType=content_type, IfNoneMatch="*"
    )


def existing(
    stub,
    storage,
    key,
    requested=b"original",
    saved=b"original",
    content_type="text/plain",
    **changes,
):
    stub.add_client_error(
        "put_object",
        service_error_code="PreconditionFailed",
        http_status_code=412,
        expected_params=put_params(storage, key, requested),
    )
    stream = io.BytesIO(saved)
    reply = dict(
        Body=StreamingBody(stream, len(saved)), ContentLength=len(saved), ContentType=content_type
    )
    reply.update(changes)
    stub.add_response("get_object", reply, dict(Bucket=storage.bucket, Key=key))
    return stream


def test_first_write_is_conditional_and_identical_retry_reads_actual_bytes(storage):
    client, stub = storage
    stub.add_response("put_object", {}, put_params(client, "a"))
    client.put("a", b"original", "text/plain")
    stream = existing(stub, client, "a")
    client.put("a", b"original", "text/plain")
    assert stream.closed


@pytest.mark.parametrize(
    "saved,type_",
    [(b"replaced", "text/plain"), (b"longer content", "text/plain"), (b"original", "image/png")],
)
def test_same_id_different_bytes_size_or_type_never_writes_again(storage, saved, type_):
    client, stub = storage
    stream = existing(stub, client, "a", saved=saved, content_type=type_)
    with pytest.raises(AttachmentConflict):
        client.put("a", b"original", "text/plain")
    assert stream.closed


@pytest.mark.parametrize(
    "code,status",
    [
        ("ConditionalRequestConflict", 409),
        ("AccessDenied", 403),
        ("NotImplemented", 501),
        ("UnknownFailure", 412),
    ],
)
def test_uncertain_put_is_not_treated_as_duplicate_or_retried_without_condition(
    storage, code, status
):
    client, stub = storage
    stub.add_client_error(
        "put_object",
        service_error_code=code,
        http_status_code=status,
        expected_params=put_params(client, "a"),
    )
    with pytest.raises(AttachmentUnavailable):
        client.put("a", b"original", "text/plain")


def test_failed_duplicate_read_is_unknown_not_success(storage):
    client, stub = storage
    stub.add_client_error(
        "put_object",
        service_error_code="PreconditionFailed",
        http_status_code=412,
        expected_params=put_params(client, "a"),
    )
    stub.add_client_error(
        "get_object",
        service_error_code="AccessDenied",
        http_status_code=403,
        expected_params=dict(Bucket=client.bucket, Key="a"),
    )
    with pytest.raises(AttachmentUnavailable):
        client.put("a", b"original", "text/plain")


def test_lost_put_response_is_unknown(monkeypatch, storage):
    client, _ = storage

    def lost(**kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        raise EndpointConnectionError(endpoint_url="https://unused.invalid")

    monkeypatch.setattr(client.client, "put_object", lost)
    with pytest.raises(AttachmentUnavailable):
        client.put("a", b"original", "text/plain")


def test_malformed_or_truncated_existing_object_is_not_success(storage):
    client, stub = storage
    stream = existing(stub, client, "a", saved=b"short", ContentLength=8)
    with pytest.raises(AttachmentUnavailable):
        client.put("a", b"original", "text/plain")
    assert stream.closed


def test_http_conflict_keeps_original_metadata_and_unconfirmed_storage_is_retryable(storage):
    client, stub = storage
    settings = Settings("local", "unused", 5432, "unused", "unused", "unused", None, False, None)
    store = FakeStore()
    key = str(uuid.uuid4())
    object_key = f"attachments/{key}"
    stub.add_response("put_object", {}, put_params(client, object_key))
    stream = existing(stub, client, object_key, requested=b"replaced")
    stub.add_client_error(
        "put_object",
        service_error_code="ConditionalRequestConflict",
        http_status_code=409,
        expected_params=put_params(client, object_key),
    )
    with TestClient(create_app(settings, store, client)) as http:
        first = http.put(
            f"/api/attachments/{key}", files={"file": ("old.txt", b"original", "text/plain")}
        )
        assert first.status_code == 200
        original = dict(store.attachments)
        conflict = http.put(
            f"/api/attachments/{key}", files={"file": ("same-size.txt", b"replaced", "text/plain")}
        )
        assert conflict.status_code == 409
        assert "Retry-After" not in conflict.headers
        uncertain = http.put(
            f"/api/attachments/{key}", files={"file": ("old.txt", b"original", "text/plain")}
        )
        assert uncertain.status_code == 503
        assert uncertain.headers["Retry-After"] == "2"
        assert store.attachments == original
    assert stream.closed
