import json

import boto3


def fetch_db_credentials(secret_id: str) -> tuple[str, str]:
    """DB 사용자와 비밀번호를 Secrets Manager에서 직접 읽는다.

    플랫폼이 찾아서 OpenBao · ESO 주입으로 고칠 "코드 수정" 등급 의존성으로 일부러 남긴 호출이다(설계 문서 8.10).
    미리 추상화하거나 환경변수로 바꾸지 않는다.
    """
    client = boto3.client("secretsmanager")
    secret = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
    return secret["username"], secret["password"]
