# 제15회 변호사시험 선택형: 모델 × 추론 강도 격자 실험

> 벤치마크: `bar-exam-15` (150문항, 375점 만점, 문항당 2.5점)
> 실행: 구독 OAuth (ChatGPT / Claude), 단일 턴, question 모드
> 점수: **v1** = strict 파서(형식 준수 포함, 공식) / **v2** = 산문 폴백 파서(병행 표기). v2 생략 시 v1과 동일.
> 실행일: 2026-07-24 ~ 2026-07-26

## 점수 격자 (v1 / v2)

| 모델 | none (비추론) | high | max |
|---|---|---|---|
| Claude Fable 5 | — | 355.0 | **362.5** |
| Claude Opus 5 | 307.5 / 340.0 | 350.0 / 352.5 | 357.5 |
| GPT-5.6 Sol | 262.5 | 307.5 | 307.5 |
| Claude Opus 4.8 | 297.5 | 292.5 / 297.5 | 300.0 / 315.0 |
| Claude Sonnet 5 | 237.5 / 245.0 | 232.5 / 287.5 | 267.5 ¹ |
| GPT-5.6 Luna | 172.5 | 242.5 | 222.5 ² |
| GPT-5.6 Terra | 230.0 | 230.0 | 237.5 ³ |

¹ Sonnet max: 18문항이 128K 출력 한도를 전부 소진하고도 답 미도출(no_answer, 0점 처리). 완주 132/150.
² Luna max: 25문항이 ChatGPT Codex 백엔드의 **스트림 수명 한도**로 영구 실패(`Response ended prematurely`). 절단 시점은 고정이 아니라 648~901초로 변동 실측. 우회 시도 결과: `service_tier: "priority"`는 HTTP 200 수락되지만 절단을 막지 못함(648초 동일 증상), `store: true` + `background: true`(서버 저장 후 폴링)는 **400 `Store must be set to false`로 거부** — 비공개 Codex 엔드포인트는 저장 자체를 금지하므로 background 우회가 원천 불가. 구독 OAuth 전송 경로에서는 회복 수단 없음이 확정. 125/150만 채점.
³ Terra max: 같은 한도로 3문항 실패(civil-law 16·51, public-law 9). 147/150 채점.

## API 환산 비용 / 출력 토큰

| 모델 | none | high | max |
|---|---|---|---|
| Fable 5 | — | $15.31 / 278K | $71.13 / 1.39M |
| Sol | $1.61 / 40K | $17.19 / 559K | $45.69 / 1.51M |
| Opus 4.8 | $3.16 / 98K | $3.17 / 99K | $5.55 / 194K |
| Sonnet 5 | $2.39 / 131K | $10.99 / 705K | $123.91 / 8.23M ⁴ |
| Luna | $0.31 / 37K | $12.37 / 2.05M | $18.91 / 3.14M |
| Terra | $0.76 / 37K | $8.50 / 553K | $41.85 / 2.78M |

⁴ 폐기된 1차 시도(65K 한도, $50.28) 별도. Sonnet max 총 소모는 약 $174.

## 주요 발견

1. **Fable 5는 모든 조건에서 1위.** max 362.5(96.7%), high로 낮춰도 355.0을 비용 1/5($15)로 달성.
2. **Opus 4.8은 effort 둔감 + 압도적 가성비.** none→max 점수 폭이 297.5→315.0(v2)에 불과하고 비용은 전부 $3~6. 비추론(297.5)이 high 공식점수(292.5)보다 높음.
3. **Sonnet 5는 max에서 병리적 overthinking.** 출력 8.2M 토큰(문항 평균 55K), 18문항은 128K를 태우고도 무응답. 점수도 max 267.5로 Opus none(297.5)에 못 미침. 세 Anthropic 모델 중 유일하게 thinking 확대가 역효과.
4. **Sol은 effort에 거의 무반응.** max=high=307.5 동점, none에서만 -45. 추론 예산이 최소한만 있으면 충분한 타입.
5. **Luna는 추론 의존도가 가장 큼.** none 172.5 → high 242.5 (+70). max는 스트림 한도로 오히려 불완주.
6. **Terra는 이상하리만치 평평.** none=high=230.0, max +7.5. 중간 티어인데 Luna high(242.5)에 밀림.
7. **형식 준수(v1)와 지식(v2)의 괴리는 Sonnet high에서 최대** (232.5 vs 287.5, 31문항). Anthropic 계열 + 중간 effort 조합에서 "마지막 줄 정답: N" 지시 미준수가 집중 발생.
8. **전송 인프라가 상한을 만든다**: ChatGPT OAuth 스트림 ~15분 한도(Luna/Terra max), Anthropic 5시간 세션 사용량 한도(429), 128K 출력 한도(Sonnet max)가 각각 실측됨.

## 참고: Gemini (API 키 실행, 별도 트랙 — 6칸 완성)

| 모델 | low | high |
|---|---|---|
| Gemini 3.1 Pro Preview | 330.0 ($2.30 / 179K) | 337.5 ($7.91 / 646K) |
| Gemini 3.6 Flash | 312.5 ($1.03 / 121K) | 317.5 ($5.88 / 768K) |
| Gemini 3.5 Flash-Lite | 210.0 ($0.38 / 142K) | 200.0 ($2.18 / 864K) |

전 셀 150/150 완주, v1=v2 (형식 위반 0 — 세 트랙 중 유일). 관찰:
- **3.1 Pro high(337.5)는 전체 3위권** (Fable, Opus 5 다음). low로 낮춰도 -7.5점뿐인데 비용 1/3.4.
- **3.6 Flash low가 최고 가성비 후보**: 312.5점(Sol max 307.5 초과)을 **$1.03**에 달성 — 점당 $0.003.
- **Flash-Lite는 low가 high보다 높음**(210 vs 200): 3.5세대 소형 모델은 thinking 확대가 역효과 — Sonnet max 패턴의 축소판.
- Gemini는 thinking_level(minimal~high) 체계라 본 격자의 effort(none~max)와 축이 정확히 일치하지 않음. 실행 경로도 API 키(Google provider)로 OAuth 트랙과 다름.
