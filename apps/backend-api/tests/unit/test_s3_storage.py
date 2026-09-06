"""S3RecordingStorage — shared by call recordings, hiree onboarding documents,
and deal recordings/consent forms. Covers both real AWS S3 and an S3-compatible
alt provider (Railway Buckets / R2 / MinIO), since they diverge on two points:
a custom endpoint, and unsupported params (e.g. Railway Buckets rejects
ServerSideEncryption)."""

from unittest.mock import MagicMock, patch

from app.calls.s3_storage import S3RecordingStorage


def test_real_aws_has_no_endpoint_override(monkeypatch):
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_BUCKET", "my-bucket")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_REGION", "us-east-1")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_ENDPOINT_URL", "")

    s3 = S3RecordingStorage()
    assert s3.configured() is True
    assert s3.endpoint_url is None
    assert s3._is_alt_provider is False

    client = s3._client()
    assert client.meta.endpoint_url == "https://s3.amazonaws.com"


def test_real_aws_upload_includes_server_side_encryption(monkeypatch):
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_BUCKET", "my-bucket")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_ENDPOINT_URL", "")

    s3 = S3RecordingStorage()
    with patch.object(s3, "_client") as mock_client:
        mock_client.return_value.put_object = MagicMock()
        s3.upload_bytes(b"data", "some/key.mp3")
        kwargs = mock_client.return_value.put_object.call_args.kwargs
    assert kwargs["ServerSideEncryption"] == "AES256"


def test_alt_provider_sets_custom_endpoint_and_region(monkeypatch):
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_BUCKET", "iag-media-abc123")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_REGION", "auto")
    monkeypatch.setattr(
        "app.calls.s3_storage.settings.S3_ENDPOINT_URL", "https://t3.storageapi.dev"
    )

    s3 = S3RecordingStorage()
    assert s3.configured() is True
    assert s3.endpoint_url == "https://t3.storageapi.dev"
    assert s3._is_alt_provider is True

    client = s3._client()
    assert client.meta.endpoint_url == "https://t3.storageapi.dev"
    assert client.meta.region_name == "auto"


def test_alt_provider_upload_omits_server_side_encryption(monkeypatch):
    # Railway Buckets (and several other S3-compatible providers) reject or
    # no-op ServerSideEncryption. Sending it anyway is the regression this test
    # guards against.
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_BUCKET", "iag-media-abc123")
    monkeypatch.setattr(
        "app.calls.s3_storage.settings.S3_ENDPOINT_URL", "https://t3.storageapi.dev"
    )

    s3 = S3RecordingStorage()
    with patch.object(s3, "_client") as mock_client:
        mock_client.return_value.put_object = MagicMock()
        s3.upload_bytes(b"data", "onboarding/some-doc.pdf", content_type="application/pdf")
        kwargs = mock_client.return_value.put_object.call_args.kwargs
    assert "ServerSideEncryption" not in kwargs
    assert kwargs == {
        "Bucket": "iag-media-abc123",
        "Key": "onboarding/some-doc.pdf",
        "Body": b"data",
        "ContentType": "application/pdf",
    }


def test_unconfigured_reports_false_and_raises_on_upload(monkeypatch):
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_ACCESS_KEY_ID", "")
    monkeypatch.setattr("app.calls.s3_storage.settings.AWS_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr("app.calls.s3_storage.settings.S3_BUCKET", "")

    s3 = S3RecordingStorage()
    assert s3.configured() is False
    try:
        s3.upload_bytes(b"data", "k")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
