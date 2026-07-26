# 공통 L2 유동성과 주문흐름 동조화 OOS 결과

## 연구 질문

OKX 7자산의 동시 공격적 주문흐름 중 일부가 공통 spread 확대, depth 고갈, book imbalance로 설명되는지 봉인된 180일 설계에서 검정했다.

## 표본 품질

- OOS 제외일 비율: 2.50% (중단 기준 10% 이하)
- 분석 timestamp: development 5,371, OOS 11,229
- 모든 변환 경계, 표준화 계수와 회귀계수는 development에서만 추정해 OOS에 고정했다.

## Primary 결과

- baseline 평균 자산쌍 상관: 0.12973
- 유동성 조건화 후 상관: 0.12969
- 상관 감소량: 0.00004 (사전 최소효과 0.02000)
- UTC-day bootstrap 95% 구간: [-0.00027, 0.00037]
- 첫째/둘째 OOS 절반 감소량: -0.00008 / -0.00015
- 단측 p=0.404, BH-FDR q=0.792
- 판정: 지지되지 않음

## Secondary 결과

- extreme-flow event: 1,331건
- 동시 liquidity-stress event: 132건
- 실제 overlap 비율: 9.92%, shift-null 평균: 10.61%
- risk ratio: 0.935 (사전 최소 1.25)
- 단측 p=0.792, BH-FDR q=0.792
- 판정: 지지되지 않음

## 해석

단순한 contemporaneous L2 유동성 3요인은 주문흐름 동조화를 실질적으로 설명하지 못했다. 상관 감소량은 사전 최소효과에 크게 못 미쳤고 두 OOS 절반에서 모두 음수였다.
이 결과는 contemporaneous 설명 연구다. intentional herding, 인과관계, 선도성 또는 미래수익률 alpha를 검정하거나 입증하지 않는다.
