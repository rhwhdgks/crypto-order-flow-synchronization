# OKX Order-Flow Synchronization 외부검증 보고서

## 한 문장 결론

Binance에서 관찰한 시장 전체 주문흐름 동조화는 OKX에서도 동일한 사전 기준을 통과했습니다.
이 결과는 거래소 외부타당성에 관한 것이며 의도적 모방이나 미래수익률 alpha를 뜻하지 않습니다.

## 데이터와 품질

- 출처: OKX 공식 Historical Market Data spot trade history
- 공통 15분 bucket: 70,080개
- 전체 grid 대비 공통 coverage: 100.0000%
- 자산: BTC-USDT, ETH-USDT, XRP-USDT, SOL-USDT, DOGE-USDT, ADA-USDT, AVAX-USDT
- 뉴스·Reddit·Twitter/X·sentiment 사용 안 함

## 1. OKX Spot 동조화

| 지표 | 실제 | null 평균 | null p95 | BH q | 효과크기 | 판정 |
|---|---:|---:|---:|---:|---|---|
| mean_pairwise_correlation | 0.11857 | 0.00210 | 0.00569 | 0.002 | 통과 | 통과 |
| extreme_alignment_rate | 0.24221 | 0.12802 | 0.13191 | 0.002 | 통과 | 통과 |

- OKX provider external replication: **통과**
- Null은 반기 내 UTC 날짜 block을 자산별로 독립 순환 이동해 자기상관과 일중 패턴을 보존했습니다.

## 2. Major↔Alt lead-lag

| 방향 | horizon | beta | HAC t | BH q | OOS 전반 | OOS 후반 |
|---|---:|---:|---:|---:|---:|---:|
| major_to_alt | 15m | 0.00127 | 0.191 | 0.8483 | 0.01798 | -0.01535 |
| alt_to_major | 15m | -0.00314 | -0.516 | 0.7268 | -0.00289 | -0.00555 |
| major_to_alt | 30m | -0.01996 | -3.279 | 0.00626 | -0.01638 | -0.02613 |
| alt_to_major | 30m | -0.01680 | -2.782 | 0.0108 | -0.02084 | -0.01601 |
| major_to_alt | 60m | -0.01950 | -2.919 | 0.01052 | -0.01059 | -0.03087 |
| alt_to_major | 60m | -0.00487 | -0.839 | 0.6021 | -0.00543 | -0.00701 |

- 15분 방향차 bootstrap 95% CI: [-0.01295, 0.02253]
- OKX 15분 major→alt cascade: **미통과**

## 3. Binance와 OKX 비교

| 지표 | Binance | OKX | OKX/Binance |
|---|---:|---:|---:|
| mean_pairwise_correlation | 0.14345 | 0.11857 | 0.827 |
| extreme_alignment_rate | 0.26524 | 0.24221 | 0.913 |

## 해석 제한

- OKX trade history와 Binance aggTrades는 체결 집계 단위가 다를 수 있어 transaction count 수준은 직접 비교하지 않았습니다.
- 두 거래소에서 반복되더라도 계정·지갑 ID가 없어 intentional herding을 직접 식별할 수 없습니다.
- 미래수익률, 거래비용, Sharpe ratio와 자동매매 가능성은 검정하지 않았습니다.
- 뉴스, Reddit, Twitter/X, sentiment는 입력·필터·해석에 사용하지 않았습니다.

## 그림

- `outputs/v2/okx_order_flow_external_validation_v1/plots/okx_synchronization_observed_vs_null.png`
- `outputs/v2/okx_order_flow_external_validation_v1/plots/okx_pairwise_residual_correlations.png`
- `outputs/v2/okx_order_flow_external_validation_v1/plots/okx_major_alt_lead_lag.png`
