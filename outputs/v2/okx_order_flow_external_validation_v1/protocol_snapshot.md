# OKX Order-Flow Synchronization 외부검증 프로토콜 v1

- 동결일: 2026-07-21
- 연구 지위: Binance order-flow synchronization v1 결과의 provider external validation
- 사전 확인 범위: OKX 공식 자료실 가용 기간, 1개 BTC-USDT 일별 ZIP의 스키마·용량·taker side만 확인했다. OKX 동조화·null·lead-lag 통계는 관찰하지 않았다.
- 명시적 제외: 뉴스, Reddit, Twitter/X, sentiment, 미래수익률 alpha, 거래전략, tracker, 자동매매

## 1. 연구 질문

Binance 7자산 OOS에서 관찰한 `market-wide order-flow synchronization`이 동일 기간과 자산의 OKX spot tick history에서도 동일한 사전 판정 기준을 통과하는가?

이 연구는 거래소 외부타당성을 검증하며, 참여자의 의도적 모방을 식별하지 않는다.

## 2. 고정 데이터

- 출처: OKX 공식 Historical Market Data의 spot tick-level trade history
- 원천 스키마: `instrument_name, trade_id, side, price, size, created_time`
- `side`: OKX 공식 정의의 taker side
- 자산: BTC-USDT, ETH-USDT, XRP-USDT, SOL-USDT, DOGE-USDT, ADA-USDT, AVAX-USDT
- 전체 기간: 2024-04-08 00:00 UTC 이상, 2026-04-08 00:00 UTC 미만
- Development: 2024-04-08 00:00 UTC 이상, 2025-04-08 00:00 UTC 미만
- OOS primary: 2025-04-08 00:00 UTC 이상, 2026-04-08 00:00 UTC 미만
- 빈도: 15분 UTC bucket

다음 품질 gate를 모두 통과해야 통계 실행을 계속한다.

- ZIP·CSV가 읽기 가능하고 파일별 SHA-256·byte·row를 manifest에 저장
- `trade_id` 중복이 자산·파일 내에 없음
- `side`는 buy/sell만 포함하고 primary 필드에 결측이 없음
- 7자산 공통 15분 교집합이 전체 70,080개 UTC bucket의 99.9% 이상

품질 gate 미통과 시 자산이나 기간을 사후 제외하지 않고 `blocked_by_source_coverage` 결과를 보존한다.

## 3. 15분 특성 구축

각 자산·bucket에서 다음을 계산한다.

- `transaction_count`: 체결 행 수
- `total_quote_quantity`: `price * size` 합
- `bucket_return`: bucket 마지막 가격 / 첫 가격 - 1
- `aggressor_imbalance`: `(buy_quote - sell_quote) / (buy_quote + sell_quote)`

OKX trade row와 Binance aggTrade는 원천 집계 단위가 다를 수 있으므로 transaction count 수준을 거래소 간 직접 비교하지 않는다. 자산별 development 내 제어변수로만 사용한다.

## 4. Aggressor 잔차화

Binance v1과 동일한 항을 사용해 OKX 자산별 development 회귀를 적합한다.

```text
aggressor_imbalance_i,t =
    alpha_i
  + own_return_i,t
  + |own_return_i,t|
  + leave_one_out_market_return_i,t
  + |leave_one_out_market_return_i,t|
  + log(quote_volume_i,t)
  + log(transaction_count_i,t)
  + UTC-hour sin/cos
  + weekday fixed effects
  + residual_i,t
```

Development 계수·잔차 평균·표준편차를 OOS에 고정 적용하고 OOS에서 재학습하지 않는다.

## 5. 교차자산 동조화 Primary

Binance v1과 동일한 두 지표를 한 BH-FDR family로 고정한다.

1. OOS 7자산 residual의 21개 pairwise correlation 평균
2. 같은 15분에 7자산 중 최소 6개 residual 부호가 일치하는 비율

시간 null은 OOS를 calendar half-year로 나누고, 각 half-year 내에서 자산별 하루 96개 15분 residual block을 독립 circular shift한다.

- 최소 shift 7일
- 499회
- seed 20260723
- one-sided empirical p-value 후 2개 family BH-FDR

### 외부검증 통과 gate

두 지표가 모두 다음을 충족해야 `okx_order_flow_synchronization_replicated`로 판정한다.

- BH q <= 0.05
- observed > null 95th percentile
- correlation observed-minus-null-mean >= 0.02
- 6/7 alignment observed/null-mean ratio >= 1.10

Binance 수치와의 크기 차이는 기술하지만 OKX 통과를 구제하거나 무효화하는 추가 gate로 사용하지 않는다.

## 6. Major↔Alt 선행 강건성

Binance v1과 동일하게 major는 BTC·ETH 평균, alt는 XRP·SOL·DOGE·ADA·AVAX 평균으로 정의한다. OOS 15·30·60분의 양방향 6개 계수를 HAC maxlag 96으로 검정하고 BH-FDR 보정한다. UTC-day bootstrap 499회, seed 20260724로 `beta_MA-beta_AM` interval을 계산한다.

15분 major→alt는 다음 모두를 충족할 때만 OKX에서 지지된다.

- standardized beta >= 0.02
- BH q <= 0.05
- OOS 전반부·후반부 beta 모두 양수
- `beta_MA-beta_AM` bootstrap 95% lower > 0

Binance의 cascade 미통과를 OKX 결과로 뒤집지 않고 provider 이질성으로 별도 보고한다.

## 7. 종합 해석

- Primary 통과: Binance 동조화의 OKX provider external replication
- Primary 미통과: Binance 동조화는 provider-specific 가능성을 보존
- 어느 경우도 intentional herding, 인과적 모방, 미래수익률 alpha로 확대하지 않음
- 뉴스·Reddit·Twitter/X·sentiment를 사후 필터로 추가하지 않음

## 8. 사후 변경 금지

- 결과 확인 후 자산, 기간, 빈도, residual controls, 6/7 기준, null·bootstrap 횟수, 최소효과를 바꾸지 않는다.
- 누락된 파일이 있으면 재시도·품질 보고 후 중단하며 유리한 소수 기간만 선택하지 않는다.
