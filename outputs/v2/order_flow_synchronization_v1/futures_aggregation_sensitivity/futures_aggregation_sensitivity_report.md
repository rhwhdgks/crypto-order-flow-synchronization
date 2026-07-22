# Futures 15분 집계 민감도 검증

## 목적

이 검증은 primary 결과를 확인한 뒤 실행한 사후 민감도 분석입니다. 기존 15분 평균을 바꾸지 않고, 각 15분 버킷의 마지막 futures taker 스냅샷을 사용했을 때도 결론이 유지되는지만 확인합니다.

## 결과

- 평균 집계 regression beta: 0.37218
- 마지막 스냅샷 regression beta: 0.28003
- 평균 집계 극단 event 방향 일치율: 77.62%
- 마지막 스냅샷 극단 event 방향 일치율: 71.65%
- Primary gate: **통과**
- Last-snapshot sensitivity gate: **통과**

## 해석

두 집계법의 gate 판정이 같으면, 일부 불규칙한 source snapshot 개수가 futures confirmation 결론을 만든 것은 아닙니다. 이 민감도 검증은 사전등록 primary를 대체하지 않으며 추가 검증으로만 보고합니다.

- 뉴스·Reddit·sentiment는 사용하지 않았습니다.
- 미래수익률 alpha는 검정하지 않았습니다.
