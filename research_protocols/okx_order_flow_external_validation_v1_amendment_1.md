# OKX Order-Flow External Validation v1 Source-Timezone Amendment 1

- 기록 시각: 2026-07-21
- 원 seal SHA-256 파일: `okx_order_flow_external_validation_v1.pre_timezone.seal.json`
- 결과 관찰 여부: OKX synchronization, null, lead-lag 통계를 계산·저장·관찰하기 전

## 발견된 원천 특성

OKX 공식 `BTC-USDT-trades-2025-04-08.zip` 스키마 파일럿에서 `created_time`은 2025-04-07 16:00 UTC 이상 2025-04-08 16:00 UTC 미만이었다. 즉 원천 일·월 파일명은 UTC+8 달력을 사용한다.

## 변경

- 원천 파일 날짜·월 경계 검증에 `Asia/Shanghai` (UTC+8)을 사용한다.
- 원천 timestamp는 즉시 UTC로 변환하고, 모든 15분 bucket·development/OOS 분할·null·lead-lag는 원 프로토콜대로 UTC를 사용한다.
- 정확한 UTC 분석 구간은 2024-04-08 이상 2026-04-08 미만으로 변경하지 않는다.

## 변경하지 않는 항목

- 자산, 기간, 15분 빈도, development/OOS 경계
- residual controls, 499회 반기 UTC-day null, seed
- 두 primary 지표, BH-FDR, 최소효과, 판정 gate
- lead-lag·bootstrap 설계
- 뉴스·Reddit·Twitter/X·sentiment·alpha 제외

이 amendment는 결과를 유리하게 변경하는 수정이 아니라, 원천 파일명의 시간대와 내부 UTC timestamp를 올바르게 매핑하는 실행 교정이다.
