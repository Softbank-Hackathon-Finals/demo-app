"""메시지 개인정보 검사 요청을 SQS로 보낸다(T46). AWS에서는 SQS → Lambda가, 목적지에서는 SQS 호환 서버 →
호환 런타임이 같은 핸들러를 실행한다. 앱은 AWS SDK를 그대로 쓰고, 목적지에서는 `AWS_ENDPOINT_URL_SQS`만 바꾼다.

큐에는 메시지 행 번호만 넣는다. 본문은 핸들러가 DB에서 읽는다. 큐에 개인정보가 남지 않는다.
"""

import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class EventUnavailable(Exception):
    """검사 요청을 보냈는지 확인할 수 없다. 클라이언트가 같은 멱등 키로 다시 보내면 다시 보낸다."""


class MessageEvents:
    def __init__(self, queue_url: str):
        self.queue_url = queue_url
        self.client = boto3.client(
            "sqs",
            config=Config(connect_timeout=2, read_timeout=3, retries={"mode": "standard", "total_max_attempts": 2}),
        )

    def message_created(self, message_id: int) -> None:
        try:
            self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps({"message_id": message_id}))
        except (BotoCoreError, ClientError) as error:
            raise EventUnavailable(type(error).__name__) from error
