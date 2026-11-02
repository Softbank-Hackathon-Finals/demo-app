-- 로컬 compose 전용. 클러스터에서는 CNPG가 앱 역할과 비밀번호를 만든다.
CREATE ROLE chat_app LOGIN PASSWORD 'local-only-app';
