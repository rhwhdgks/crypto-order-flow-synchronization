# 교차자산 Order-Flow Synchronization 연구 보고서

## 한 문장 결론

최종 분류는 `synchronization_supported_without_directional_cascade`입니다. 이 결과는 시장 전체 주문흐름 동조화에 관한 것이며 투자자의 의도적 모방이나 미래수익률 alpha를 검정하지 않습니다.

## 데이터

- Spot rows: 490,560, timestamps: 70,080, assets: 7
- 기간: 2024-04-08 00:00:00+00:00 ~ 2026-04-07 23:45:00+00:00
- Aggressor 가용률: 100.00%
- Development 1년에서 잔차화 계수와 scaler를 적합하고 OOS 1년에 고정 적용
- 뉴스·Reddit·sentiment 사용 안 함

## 1. Spot 교차자산 동조화

| 지표 | 실제 | null 평균 | null p95 | BH q | 효과크기 gate | 판정 |
|---|---:|---:|---:|---:|---|---|
| mean_pairwise_correlation | 0.14345 | 0.00114 | 0.00468 | 0.002 | 통과 | 통과 |
| extreme_alignment_rate | 0.26524 | 0.12628 | 0.13040 | 0.002 | 통과 | 통과 |

- Spot primary: **지지**
- Null은 반기 내 UTC 날짜 block을 자산별로 독립 circular shift해 자기상관과 일중 패턴을 보존했습니다.

## 2. BTC·ETH에서 알트코인으로의 전파

| 방향 | horizon | beta | HAC t | BH q | 전반부 beta | 후반부 beta |
|---|---:|---:|---:|---:|---:|---:|
| major_to_alt | 15m | 0.01442 | 2.347 | 0.03785 | 0.02752 | 0.00285 |
| alt_to_major | 15m | -0.00283 | -0.435 | 0.6637 | -0.02132 | 0.01293 |
| major_to_alt | 30m | -0.00304 | -0.479 | 0.6637 | -0.00577 | -0.00073 |
| alt_to_major | 30m | -0.02085 | -2.876 | 0.01208 | -0.02672 | -0.01636 |
| major_to_alt | 60m | -0.00687 | -1.034 | 0.4515 | -0.00245 | -0.01110 |
| alt_to_major | 60m | -0.02163 | -3.433 | 0.003575 | -0.03173 | -0.01303 |

방향차 bootstrap:

| horizon | MA-AM 평균 | 95% CI | one-sided p |
|---:|---:|---:|---:|
| 15m | 0.01670 | [0.00041, 0.03544] | 0.026 |
| 30m | 0.01750 | [-0.00009, 0.03480] | 0.028 |
| 60m | 0.01491 | [-0.00199, 0.03083] | 0.056 |

- 15분 major→alt cascade: **지지하지 않음**

## 3. 선물 확인

| 검정 | n | 추정값 | p | BH q |
|---|---:|---:|---:|---:|
| spot_to_futures_flow_regression | 35,038 | 0.37218 | 1.428e-171 | 1.428e-171 |
| extreme_event_direction_concordance | 3,164 | 0.77623 | 2.775e-224 | 5.55e-224 |

- Spot-flow 절대값과 절대 OI 변화 상관: -0.0300
- Futures confirmation: **지지**

## 해석 제한

- 통과 결과가 있더라도 `market-wide order-flow synchronization` 또는 `order-flow cascade`로만 부릅니다.
- aggTrades에는 계정·지갑 ID가 없어 intentional imitation을 직접 식별할 수 없습니다.
- 미래수익률, 거래비용, 전략 성과를 검정하지 않았습니다.
- 뉴스, Reddit, sentiment는 입력·필터·해석에 사용하지 않았습니다.

## 그림

- `outputs/v2/order_flow_synchronization_v1/plots/synchronization_observed_vs_null.png`
- `outputs/v2/order_flow_synchronization_v1/plots/pairwise_residual_correlations.png`
- `outputs/v2/order_flow_synchronization_v1/plots/major_alt_lead_lag.png`
