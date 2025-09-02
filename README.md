# jungle-mini-project
11기 정글 미니 프로젝트

Docker -> docker compose

## 설치
* Docker Destop


## 실행 방법
docker compose up -d  --build

## 실행 확인
* localhost:5001 -> flask app
* localhost:8081 -> mongo UI
* localhost:8081/db/ping -> DB 확인
* secrets 폴더 생성후 openai_api_key.txt 만들기 & OPENAI 키 복붙

## LeetCode 가져오기 실패 시 대처
GraphQL 호출이 일부 환경에서 차단될 수 있습니다. 성공률을 높이려면 아래 환경 변수를 설정하세요(브라우저에서 leetcode.com 쿠키 값 복사).

- LEETCODE_SESSION: 브라우저의 LEETCODE_SESSION 쿠키
- LEETCODE_CSRF: 브라우저의 csrftoken 쿠키
- LEETCODE_CN_FALLBACK: "true"로 설정 시 leetcode.com 실패 시 leetcode.cn GraphQL로 폴백 시도

docker-compose.yml의 web.environment에 값을 채우고 재기동하면 됩니다.
