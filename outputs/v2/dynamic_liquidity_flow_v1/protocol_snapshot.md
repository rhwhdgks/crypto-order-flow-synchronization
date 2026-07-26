# Dynamic L2 Liquidity and Common Order Flow Protocol v1

## 배경

`common_liquidity_order_flow_v1`은 공통 L2 spread, depth depletion, book imbalance가
동시점 주문흐름 동조화를 실질적으로 설명하지 못한다는 OOS 결과를 냈다. 이 결과는
확인했지만, 아래의 시차 예측 결과는 protocol 작성 시점에 계산하거나 관찰하지 않았다.

## 연구 질문

현재 L2 유동성 상태가 15분, 30분, 60분 뒤 시장 공통 공격적 주문흐름의 **크기**를
예측하는가? 반대로 현재 주문흐름 크기가 이후 L2 유동성 stress를 예측하는지도 대칭적으로
검정한다. 미래수익률이나 거래손익은 사용하지 않는다.

## 입력과 표본

- 입력: 봉인된 `common_liquidity_order_flow_v1`이 development에서만 변환한 공통 feature
- 빈도: 15분
- Development: 2025-10-08 포함, 2025-12-07 미포함
- OOS: 2025-12-07 포함, 2026-04-06 미포함
- Horizon: 15분, 30분, 60분
- 누락 구간을 건너 target을 만들지 않으며 source와 target이 같은 split에 있을 때만 사용

## Forward model

Target은 `|common_flow_(t+h)|`다.

- Baseline: intercept, `|common_flow_t|`, UTC 시간 통제
- Augmented: baseline + `common_spread_t`, `common_depth_depletion_t`,
  `common_abs_imbalance_t`

UTC 시간 통제는 하루 주기의 sine/cosine과 월요일을 기준으로 한 요일 dummy 6개다. 모든
회귀계수는 development에서 OLS로 적합하고 OOS에 그대로 고정한다.

## Reverse model

Target은 `liquidity_stress_(t+h)`다.

- Baseline: intercept, `liquidity_stress_t`, UTC 시간 통제
- Augmented: baseline + `|common_flow_t|`

Forward와 같은 development/OOS 규칙을 사용한다. 역방향은 선후행 해석의 대칭 비교이며
forward primary 판정을 대체하지 않는다.

## OOS 효과와 통계

각 방향·horizon의 효과는 다음 OOS MSE 개선율이다.

`effect = 1 - MSE_augmented / MSE_baseline`

OOS UTC 날짜를 paired block으로 499회 복원추출해 단측 p-value와 95% interval을
계산한다. 6개 방향-horizon 검정에 BH-FDR을 적용한다.

Primary는 `liquidity_to_flow`, 15분이다. 다음을 모두 만족해야 지지한다.

- BH-FDR q-value <= 0.05
- OOS MSE 개선율 >= 1%
- 첫째와 둘째 OOS 절반의 개선율이 모두 양수

30분·60분 forward와 세 reverse 검정은 secondary다. 결과 확인 후 horizon, target,
통제변수, 효과크기 기준을 바꾸지 않는다.

## 해석 제한

통과 시 L2 상태가 이후 주문흐름 크기에 incremental predictive information을 제공한다는
뜻이다. causal effect, intentional herding, 가격 방향 또는 거래 가능한 alpha를 뜻하지
않는다. 뉴스, Reddit, Twitter/X, sentiment와 참여자 ID를 사용하지 않는다.
