# Order-Flow Synchronization v1 Futures 품질 Amendment 2

- 기록 시각: 2026-07-21
- 직전 seal SHA-256: `aff013ad036de418ccdb21f4ad7a530bf515ad20a58404cd168cc4401115fe6d`
- 결과 관찰 여부: 결과 파일과 통계값은 저장·출력되지 않았고 futures 원천 결측 검증에서 중단

## 확인된 원천 품질

2024-04-08 이상 2026-04-08 미만의 각 5개 선물자산은 210,235개 5분 source row를 가진다.

- Primary `sum_open_interest`: 결측 0
- Primary `sum_taker_long_short_vol_ratio`: 결측 0
- Descriptive account-ratio 계열: 자산별 28~64개 결측

## 변경

- Primary 두 변수에는 기존과 동일하게 결측 0을 요구한다.
- Gate에 사용하지 않는 account-ratio 변수는 결측을 허용하고 15분 bucket의 마지막 비결측값으로 기술 집계한다.
- 파일별 결측 수를 futures manifest에 저장한다.
- Account-ratio를 대체·보간하지 않으며 결과 gate에 추가하지 않는다.

이 변경은 primary 검정이나 유의성 기준을 바꾸지 않고, protocol에서 descriptive로 고정한 변수의 소수 원천 결측 때문에 전체 실행이 중단되는 문제만 교정한다.
