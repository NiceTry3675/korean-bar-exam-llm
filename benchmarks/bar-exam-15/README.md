# 제15회 변호사시험 선택형 벤치마크 데이터

이 디렉터리에는 문제 본문이 아니라 벤치마크 구조, 정답, 배점 및 출처
정보만 포함됩니다. 원본 HWP와 추출된 문제 본문은 저장소에서 추적하지
않으며 `scripts/prepare_bar_exam.py`를 실행한 로컬 환경의
`problems/bar-exam-15/`에만 생성됩니다.

## 범위와 채점

- 공법 40문항, 100점
- 민사법 70문항, 175점
- 형사법 40문항, 100점
- 합계 150문항, 375점(각 문항 2.5점)

이 데이터는 변호사시험 전체가 아닌 선택형 시험만 다루므로 합격 여부를
계산하는 용도로 사용할 수 없습니다.

## 출처와 이용조건

- 문제: [법무부 제15회 변호사시험 선택형 문제](https://www.moj.go.kr/bbs/moj/150/602397/artclView.do)
- 정답가안: [법무부 2026-01-10 공지](https://www.moj.go.kr/bbs/moj/151/602396/artclView.do)
- 최종정답: [법무부 2026-02-12 확정 공지](https://www.moj.go.kr/bbs/moj/151/603464/artclView.do)
- 문서 형식: [한컴 HWP 5.0 파일 형식 명세](https://cdn.hancom.com/link/docs/%ED%95%9C%EA%B8%80%EB%AC%B8%EC%84%9C%ED%8C%8C%EC%9D%BC%ED%98%95%EC%8B%9D_5.0_revision1.2.pdf)

최종 공지에서 정답가안이 변경 없이 확정되었으므로 `questions.json`은
해당 답안을 `final_confirmed` 상태로 기록합니다. 법무부 게시물의 시험
콘텐츠는 [공공누리 제1유형](https://www.kogl.or.kr/info/userGuide.do)에
따른 출처 표시가 필요합니다. 이 이용조건은 저장소 소스 코드의 MIT
라이선스와 별개입니다.

## Local preparation

This directory contains only the public benchmark structure, answer key,
scoring, and provenance. Raw HWP files and extracted question text remain in
the ignored local `problems/bar-exam-15/` directory.

The Ministry of Justice final notice confirmed the provisional answer key
without changes. Exam content is distributed under the Korea Open Government
License Type 1, which requires attribution; that content license is separate
from the repository's MIT source-code license.

다운로드한 6개 HWP는 `problems/bar-exam-15/source/`에 둡니다. 변환된
문항은 같은 ignored 루트의 과목별 디렉터리에 생성됩니다.

```bash
# 원문 검증만 수행
python3 scripts/prepare_bar_exam.py --check-only

# 검증 후 ignored 로컬 문제 파일 생성
python3 scripts/prepare_bar_exam.py

# 로컬 모델 설정 준비(비밀키 값 대신 환경변수 이름만 기록)
cp benchmark_models.example.json benchmark_models.json

# 문항별 150개 요청 미리보기: 기본값은 네트워크 없는 dry-run
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode question

# 과목별 3개 일괄 요청 미리보기
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode subject

# 문제 본문 앞부분까지 터미널에서 확인할 때만 명시적으로 추가
# --include-prompt-preview
```

## 구독 OAuth 인증

`openai-codex-oauth`와 `anthropic-oauth`는 공개 API 키 대신 사용자 구독
계정의 OAuth credential을 OS Keychain에 보관합니다. 모델 설정에는
credential 값이나 환경변수 이름을 넣지 않고 `oauth_profile`만 기록합니다.
기본 profile 이름은 `default`입니다.
벤치마크 문제 본문은 한 개의 user message로 그대로 전송됩니다. Anthropic
요청에만 과금 식별 block과 Claude Agent SDK identity block을 이 순서로
system content에 추가하며, 코딩 agent 지시나 도구는 추가하지 않습니다.
OpenAI OAuth 설정의 `max_output_tokens`는 context 사전검사용 reserve이며
Codex private Responses 요청에는 출력 제한 parameter로 전송되지 않습니다.

두 방식은 각 서비스의 소비자용 계정과 비공개 또는 전용 transport를
사용합니다. 자동 벤치마크 실행은 서비스 약관 위반, 사용 제한 또는 계정
정지로 이어질 수 있습니다. 로그인할 때 선택한 제공자에 대한 위험을 직접
확인해야 합니다. 대화형 로그인은 정확히 `ACCEPT`를 입력받고, 비대화형
로그인만 `--accept-account-risk`를 요구합니다. 한 제공자에 대한 동의는
다른 제공자에 대한 동의를 대신하지 않습니다.

```bash
# Anthropic: 안내된 인증 페이지를 연 뒤 최종 callback URL 전체를 붙여넣음
python3 benchmark_auth.py login anthropic-oauth --profile default

# OpenAI: 기본적으로 로컬 loopback callback을 사용
python3 benchmark_auth.py login openai-codex-oauth --profile default

# OpenAI loopback callback을 사용할 수 없을 때만 수동 흐름 선택
python3 benchmark_auth.py login openai-codex-oauth \
  --profile default --manual

# 저장된 credential의 존재·만료 상태만 확인하며 token 값은 출력하지 않음
python3 benchmark_auth.py status
python3 benchmark_auth.py status anthropic-oauth --profile default

# 선택한 profile의 credential 제거
python3 benchmark_auth.py logout anthropic-oauth --profile default
python3 benchmark_auth.py logout openai-codex-oauth --profile default
```

OAuth credential을 찾을 수 없거나 갱신 또는 quota 확인에 실패하면 실행은
중단됩니다. 같은 환경에 `OPENAI_API_KEY`나 `ANTHROPIC_API_KEY`가 있어도
API-key 방식으로 자동 전환하지 않습니다.

실제 API 호출은 별도의 `--execute`와 실행 상한을 지정해야만 시작됩니다.
OAuth를 포함한 모든 제공자는 `--max-requests` 또는 `--max-cost-usd` 중
하나만 지정해도 되며 함께 지정할 수도 있습니다. `--max-requests`는 제공자
요청 시도 횟수의 일반 상한이므로 비용 상한만 사용할 때는 선택 사항입니다.
구현 검증 단계에서는 `--execute`를 사용하지 않습니다.

OAuth 모델에도 `input_cost_per_million`과 `output_cost_per_million`을 숫자로
설정하면 응답의 입력·출력 token 사용량으로 `cost_usd`를 계산합니다.

```text
cost_usd =
  input_tokens / 1,000,000 × input_cost_per_million
  + output_tokens / 1,000,000 × output_cost_per_million
```

벤치마크 비교를 단순하게 유지하기 위해 cache read/write 필드는 계산에서
제외하고 `input_tokens`에 입력 단가를 적용합니다. 이 값은 구독 서비스의
실제 청구액이 아니라 공개 API 단가를 적용한 벤치마크용 API-equivalent
추정치입니다. 둘 중 한 가격이라도 `null`이면 비용은 미상으로 남으며
`--max-cost-usd` 단독 실행에는 사용할 수 없습니다. 예제의 OAuth 단가는
2026-07-24 기준 표준 API 가격으로, `gpt-5.6-sol`은 입력 $5/MTok·출력
$30/MTok, `claude-fable-5`는 입력 $10/MTok·출력 $50/MTok입니다.

실제 실행을 시작하기 전에는 예제 설정의 모델 ID·컨텍스트 한도·가격을
공급자 문서와 대조하고, `--max-requests` 또는 `--max-cost-usd`를
보수적으로 지정합니다. 실행이 끝난 뒤 `parse_failed`, `no_answer`,
`refusal` 항목을 먼저 검토한 다음에만 검증 결과를 워크북과 웹 데이터로
동기화합니다.

```bash
# 실제 실행은 명시적 플래그와 상한이 있을 때만 가능 (지금은 실행하지 않음)
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode question \
  --execute --max-requests 150

python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode subject \
  --execute --max-requests 3

# 모든 선택 모델에 input/output 단가가 있을 때는 비용 상한만 지정 가능
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode question \
  --execute --max-cost-usd 1

# 검토가 끝난 뒤 문항별/과목 일괄 결과를 각각 가져오고 공개 JSON 생성
python3 sync_data.py --benchmark bar-exam-15 import --all
python3 sync_data.py --benchmark bar-exam-15 import --all --hard
python3 sync_data.py --benchmark bar-exam-15 export --all-sheets \
  --output bar_exam_all_results.json
python3 sync_data.py --benchmark bar-exam-15 export --all-sheets --hard \
  --output bar_exam_hard_all_results.json
```

### OAuth 수동 smoke test

CI와 자동 테스트는 네트워크 및 실제 Keychain 접근을 차단하므로 OAuth
로그인은 실행하지 않습니다. 로컬에서 제공자 하나를 확인할 때만 다음
순서로 최소 1개 요청을 실행합니다.

1. 위 `login` 명령으로 별도 test profile에 로그인합니다.
2. `benchmark_models.json`에는 해당 OAuth 모델 하나와 test profile만
   남기고 model ID, context 한도 및 `requests_per_minute`를 확인합니다.
3. 먼저 `--execute` 없는 dry-run을 실행하고 요청 수와 prompt 구성을
   검토합니다.
4. `status`가 token을 노출하지 않는지 확인한 뒤
   `--execute --max-requests 1`로 문항별 요청 하나만 실행합니다.
5. checkpoint와 verified 결과에서 text·usage·stop/refusal 정규화를 확인한
   다음 같은 명령을 다시 실행해 provider call 없이 resume되는지 확인합니다.
6. 터미널, checkpoint, verified 결과 및 ignored raw 결과에 bearer,
   access token, refresh token이나 계정 식별자가 기록되지 않았는지
   확인합니다.
7. 확인이 끝나면 test profile을 `logout`으로 제거합니다.
