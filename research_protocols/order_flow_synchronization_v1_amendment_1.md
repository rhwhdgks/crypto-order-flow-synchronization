# Order-Flow Synchronization v1 실행 가능성 Amendment 1

- 기록 시각: 2026-07-21
- 원 seal SHA-256: `565d3274200b65848459b7638385fd2d15a8c515c1b6b7beb9c1ecc91028deac`
- 결과 관찰 여부: synchronization statistic과 null draw가 계산·저장되기 전에 실패

## 실패 원인

OOS는 2025-04-08 이상 2026-04-08 미만이다. Calendar quarter로 나누면 마지막 2026-Q2 block은 7일뿐이다. 최소 7일 circular shift를 유지하면서 0이 아닌 양방향 offset을 만들려면 block이 14일보다 길어야 하므로 실행이 중단됐다.

## 변경

- Calendar quarter block을 calendar half-year block으로 변경한다.
- 날짜 단위 96개 bucket 보존, 자산별 독립 shift, 최소 shift 7일, 499회, seed는 변경하지 않는다.
- Half-year block 길이는 각각 84일, 184일, 97일로 실행 가능하다.

## 변경하지 않는 항목

- 데이터, 자산, development/OOS 기간, residual controls
- 두 primary statistic과 effect-size gate
- lead-lag, bootstrap, futures confirmation
- 뉴스·Reddit·sentiment 제외
- intentional herding 및 alpha 비식별 제한

본 amendment는 결과를 본 뒤 유의성을 바꾸는 수정이 아니라, 첫 null draw 이전에 발견된 날짜 partition 실행 불가능성을 교정한다.
