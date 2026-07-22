# 교차자산 Order-Flow Synchronization 및 Cascade 프로토콜 v1

- 동결일: 2026-07-21
- 연구 지위: 기존 CSAD 식별 실패와 tick run 의미 교정 이후의 신규 미시구조 연구
- 목적: 여러 암호화폐의 공격적 매수·매도가 우연과 공통 가격충격을 넘어 동기화되는지, BTC·ETH에서 알트코인으로 주문흐름이 선행 전파되는지 검정한다.
- 용어 제한: 계정 ID가 없으므로 결과를 `intentional herding`으로 부르지 않고 `market-wide order-flow synchronization` 또는 `order-flow cascade`로만 표현한다.
- 명시적 제외: 뉴스, Reddit, sentiment, 미래수익률 alpha, 거래전략, tracker, 자동매매.

## 1. 이미 알고 있는 사실

- Binance spot 7자산 raw aggTrades를 2024-04-08 이상 2026-04-08 미만의 490,560개 15분 bucket으로 재구축했다.
- buyer-maker 기반 aggressor imbalance 가용률은 100%이고 자산별 시간 grid가 완전하다.
- 기존 `run_clustering_side`는 가격 또는 aggressor 방향 proxy가 아니며 zero-run 연구도 미래 5·15·30분 위험을 예측하지 못했다.
- 이번 연구는 run winner, zero-run threshold 또는 기존 미래수익률 가설을 재사용하지 않는다.

## 2. 고정 데이터

### Spot primary

- 자산: BTCUSDT, ETHUSDT, XRPUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT
- 빈도: 기존 schema-v2 15분 bucket
- Development: 2024-04-08 00:00 UTC 이상 2025-04-08 00:00 UTC 미만
- OOS primary: 2025-04-08 00:00 UTC 이상 2026-04-08 00:00 UTC 미만
- 주요 변수: `aggressor_imbalance`, `bucket_return`, `total_quote_quantity`, `transaction_count`
- 시장이 동시에 열린 완전 교집합만 사용하고 자산 또는 시간을 결과 확인 후 제외하지 않는다.

### Futures confirmation

- 공통자산: XRPUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT
- Binance public futures metrics의 5분 `sum_taker_long_short_vol_ratio`, open interest, account long-short ratio를 15분으로 집계한다.
- Development/OOS 경계는 spot과 동일하다.
- 선물 결과는 spot primary를 통과시키는 대체 조건으로 사용하지 않는다.
- Primary인 taker ratio와 open interest에는 결측 0을 요구한다. Gate에 쓰지 않는 account long-short ratio의 원천 결측은 파일별로 기록하고 15분 내 마지막 비결측값만 기술 집계하며 보간하지 않는다.

## 3. Aggressor 잔차화

각 자산의 development 구간에서 다음 회귀를 적합한다.

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

- development 계수와 residual 표준편차를 OOS에 그대로 적용한다.
- OOS 정보를 이용해 계수, scaler, winsor 범위 또는 변수를 다시 적합하지 않는다.
- primary 동조화 지표는 표준화 OOS residual로 계산한다.

## 4. 교차자산 동조화 Primary

두 지표를 하나의 BH-FDR family로 고정한다.

1. 7자산 residual의 21개 pairwise correlation 평균
2. 같은 15분에 7자산 중 최소 6개 residual 부호가 일치하는 비율

조건부 시간 null은 OOS를 calendar half-year로 나누고, 각 half-year 안에서 자산별 residual 전체 날짜 block을 독립적으로 circular shift한다.

- 하루 96개 15분 bucket을 그대로 보존한다.
- 최소 shift는 7일이다.
- 반복 수 499회, seed 20260721
- 자산별 자기상관·일중 패턴·반기별 분포는 보존하고 정확한 동시점 정렬만 파괴한다.

각 지표의 one-sided empirical p-value는 `(1 + null >= observed) / 500`으로 계산하고 두 개를 BH-FDR 보정한다.

### Primary gate

두 지표가 모두 아래를 충족해야 `order_flow_synchronization_supported`로 분류한다.

- BH q <= 0.05
- 실제값이 null 95th percentile 초과
- pairwise correlation의 실제-minus-null-mean >= 0.02
- 6/7 방향 일치율의 실제/null-mean 비율 >= 1.10

하나라도 실패하면 동조화의 기술통계는 보존하지만 confirmatory support로 사용하지 않는다.

## 5. BTC·ETH → Alt Cascade

Development residual scaler를 적용한 전체 기간에서 다음 composite를 만든다.

- Major flow: BTCUSDT와 ETHUSDT residual 평균
- Alt flow: XRPUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT residual 평균

OOS에서 15·30·60분 horizon별로 양방향 회귀를 수행한다.

```text
AltFlow_t+h   = alpha + beta_MA * MajorFlow_t + phi * AltFlow_t + error
MajorFlow_t+h = alpha + beta_AM * AltFlow_t   + phi * MajorFlow_t + error
```

- HAC maxlag 96
- 6개 방향×horizon 계수를 하나의 BH-FDR family로 보정
- UTC-day moving-block bootstrap 499회로 `beta_MA - beta_AM`의 95% interval 계산
- OOS 전반부와 후반부의 부호도 별도 저장

### Cascade gate

15분 major→alt만 primary endpoint다. 아래를 모두 만족해야 `major_to_alt_cascade_supported`로 분류한다.

- major→alt standardized beta >= 0.02
- major→alt BH q <= 0.05
- major→alt beta가 OOS 전반부와 후반부 모두 양수
- `beta_MA - beta_AM` bootstrap 95% interval lower > 0

30·60분 결과는 horizon decay 강건성으로만 보고하며 15분 실패를 대체하지 않는다.

## 6. Futures Confirmation

5개 공통자산에서 `sum_taker_long_short_vol_ratio`를 `(ratio-1)/(ratio+1)`로 변환하고 development 평균·표준편차로 표준화한다. Spot residual과 futures taker imbalance의 5자산 평균을 각각 composite로 만든다.

OOS에서 다음 두 검정을 하나의 BH-FDR family로 둔다.

1. `FuturesFlow_t = alpha + beta * SpotFlow_t + phi * FuturesFlow_t-1 + error_t`, HAC maxlag 96
2. Development spot common-flow 절대값 95th percentile을 넘는 OOS event에서 spot과 futures 방향 일치율의 one-sided binomial test

아래를 모두 충족해야 `futures_confirmation_supported`다.

- standardized beta >= 0.05 및 BH q <= 0.05
- event 100개 이상
- 방향 일치율 >= 0.55 및 BH q <= 0.05

Open-interest 변화와 account long-short ratio는 동시점 기술통계로만 저장하고 gate에 넣지 않는다.

## 7. 종합 판정

- Spot primary와 cascade는 서로 독립 판정한다.
- Futures confirmation은 별도 construct-validity 판정이며 spot 실패를 구제하지 않는다.
- 세 family를 모두 통과해도 결과는 시장 전체 주문흐름 동조화와 전파의 증거이지 개별 투자자의 모방 의도 증거가 아니다.
- 미래수익률, 비용, Sharpe ratio 또는 거래 가능성은 검정하지 않는다.

## 8. 사후 변경 금지

- 결과 확인 후 자산, 기간, 15분 빈도, residual controls, 6/7 기준, 95% event threshold, permutation·bootstrap 횟수, 최소 효과크기를 바꾸지 않는다.
- 1분·5분 재집계는 본 v1 결과를 본 뒤 수행하는 별도 protocol로만 가능하다.
- 뉴스·Reddit·sentiment를 사후 필터로 추가하지 않는다.
- 유의한 특정 종목·시간대·regime만 골라 broad conclusion을 만들지 않는다.

## 9. 한계

- Binance aggTrades는 계정 또는 지갑 ID를 제공하지 않아 누가 누구를 따라 했는지 직접 관측할 수 없다.
- buyer-maker는 aggressor side를 제공하지만 주문 제출·취소, 호가 depth, liquidation identity는 제공하지 않는다.
- 동시 반응은 미관측 공통정보에 의해 발생할 수 있으므로 residualization과 시간 null을 통과해도 intentional imitation으로 확대하지 않는다.
- Binance 단일 거래소와 7개 survivor asset에 한정된다.
