import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class AttachmentConflict(Exception):
    """The requested id already names different immutable object content."""


class AttachmentUnavailable(Exception):
    """The outcome is unconfirmed; retry only with the same id and content."""


class AttachmentStorage:
    """첨부파일을 S3 SDK로 저장한다. 엔드포인트만 바꾸면 S3 호환 저장소(SeaweedFS 등)로 옮겨진다("설정 교체" 등급)."""

    def __init__(self, bucket: str, endpoint_url: str | None, force_path_style: bool):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            config=Config(
                s3={"addressing_style": "path" if force_path_style else "auto"},
                connect_timeout=5,
                read_timeout=5,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if (
                error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412
                or error.response.get("Error", {}).get("Code") != "PreconditionFailed"
            ):
                # 409, authorization failure, unsupported condition or an unknown
                # response are not proof that this id contains the caller's data.
                raise AttachmentUnavailable from None
            self._verify_existing(key, data, content_type)
        except BotoCoreError:
            # A lost PUT response can mean the object was committed. Never retry
            # unconditionally or invent a fresh key to make this request succeed.
            raise AttachmentUnavailable from None

    def _verify_existing(self, key: str, data: bytes, content_type: str) -> None:
        try:
            reply = self.client.get_object(Bucket=self.bucket, Key=key)
            body = reply["Body"]
            try:
                length = reply.get("ContentLength")
                if (
                    type(length) is not int
                    or length < 0
                    or not isinstance(reply.get("ContentType"), str)
                ):
                    raise AttachmentUnavailable
                if length != len(data) or reply.get("ContentType") != content_type:
                    raise AttachmentConflict
                # Compare real bytes, not ETag or caller-controlled metadata.
                # Bound both reads and always release the HTTP connection.
                observed = body.read(len(data) + 1)
                if not isinstance(observed, bytes) or len(observed) != length:
                    raise AttachmentUnavailable
                if observed != data:
                    raise AttachmentConflict
            finally:
                body.close()
        except (BotoCoreError, ClientError, KeyError, OSError):
            raise AttachmentUnavailable from None

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
