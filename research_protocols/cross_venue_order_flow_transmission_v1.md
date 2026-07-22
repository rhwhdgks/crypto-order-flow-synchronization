# Binance-OKX 교차거래소 주문흐름 동조화 및 전파 프로토콜 v1

- 동결일: 2026-07-22
- 연구 지위: Binance 내부 동조화와 OKX provider external validation 이후의 신규 결합 연구
- 결과 관찰 상태: protocol과 config 작성 시 교차거래소 통계량은 계산하지 않음
- 목적: 동일 자산의 공격적 주문흐름이 거래소 간에도 동시에 움직이는지, 한 거래소가 다른 거래소를 안정적으로 선행하는지 검정한다.
- 명시적 제외: 뉴스, Reddit, Twitter/X, sentiment, 미래수익률, 거래전략, intentional herding 식별.

## 1. 이미 알고 있는 사실

- Binance와 OKX 각각에서 7자산의 OOS 잔차 주문흐름 동조화 gate가 통과했다.
- 각 연구는 2024-04-08 이상 2026-04-08 미만, 15분, 같은 7자산과 같은 development/OOS 경계를 사용한다.
- Binance와 OKX의 메이저→알트 15분 cascade는 지지되지 않았다.
- 본 연구 전에 두 residual 파일의 행 수, 컬럼, timestamp grid만 확인했고 교차거래소 correlation, concordance, lead-lag 계수는 계산하지 않았다.

## 2. 고정 입력

- 자산: BTCUSDT, ETHUSDT, XRPUSDT, SOLUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT
- 빈도: 15분
- Development: 2024-04-08 00:00 UTC 이상 2025-04-08 00:00 UTC 미만
- OOS primary: 2025-04-08 00:00 UTC 이상 2026-04-08 00:00 UTC 미만
- 입력: 각 선행 연구가 development에서만 적합한 `aggressor_residual_z`
- OKX의 `BTC-USDT` 형식은 Binance의 `BTCUSDT` 형식으로 기계적으로 매핑한다.
- 두 거래소·7자산의 완전 교집합만 허용하며 결과 확인 후 자산이나 시간을 제외하지 않는다.

## 3. 동시 동조화 primary family

다음 두 지표를 하나의 BH-FDR family로 고정한다.

1. 7개 동일 자산에 대한 Binance-OKX 동시점 residual correlation의 평균
2. 어느 한 거래소의 7자산 공통 흐름 절대값이 development 95th percentile을 넘는 OOS 시각에서 두 거래소 공통 흐름의 방향 일치율

공통 흐름은 각 거래소 7개 standardized residual의 단순평균이다. Event threshold는 거래소별 development에서 고정하며 OOS로 다시 적합하지 않는다.

### 시간 null

- OOS를 calendar half-year block으로 나눈다.
- 각 block에서 OKX의 7자산 패널 전체를 같은 정수 일수만큼 circular shift한다.
- 최소 shift는 7일이고 15분 일중 배열과 OKX 내부의 교차자산 동조화를 그대로 보존한다.
- 499회, seed 20260723
- 이 null은 거래소별 자기상관·일중 패턴·내부 동조화를 보존하고 정확한 Binance-OKX 시간 정렬만 파괴한다.

### 동조화 gate

두 지표가 모두 아래 조건을 충족해야 `cross_venue_synchronization_supported`다.

- BH q <= 0.05
- 실제값이 null 95th percentile 초과
- 평균 same-asset correlation의 실제-minus-null-mean >= 0.05
- extreme-event 방향 일치율의 실제/null-mean 비율 >= 1.10
- extreme event 100개 이상

## 4. 방향성 전파 family

OOS의 Binance 공통 흐름을 `B_t`, OKX 공통 흐름을 `O_t`로 두고 15·30·60분에서 양방향 회귀를 수행한다.

```text
O_t+h = alpha + beta_BO * B_t + phi * O_t + error
B_t+h = alpha + beta_OB * O_t + phi * B_t + error
```

- HAC maxlag 96
- 2방향×3 horizon의 6개 계수를 하나의 BH-FDR family로 보정
- OOS 전반부와 후반부 계수를 별도 저장
- 15분에서 UTC-day moving-block bootstrap 499회로 `beta_BO - beta_OB`의 95% interval을 계산

### 방향성 gate

사전에 어느 거래소가 선행한다고 정하지 않는다. 15분에서 아래를 만족한 방향만 지지한다.

- 해당 방향 standardized beta >= 0.02
- 해당 방향 BH q <= 0.05
- OOS 전반부와 후반부 beta가 모두 양수
- Binance→OKX는 방향차 interval lower > 0
- OKX→Binance는 방향차 interval upper < 0

두 방향이 모두 통과하거나 모두 실패하면 `stable_directional_transmission_not_supported`다.

## 5. 종합 해석

- 동시 동조화와 방향성 전파는 독립 판정한다.
- 동조화만 통과하면 두 거래소가 공통 정보 또는 시장 전체 수요충격에 동시에 반응한다는 해석과 일치한다.
- 방향성까지 통과해야 특정 거래소가 다른 거래소보다 선행한다는 제한적 증거로 표현한다.
- 어느 결과도 계정·지갑 수준의 intentional imitation을 식별하지 않는다.
- 미래수익률과 비용을 사용하지 않으므로 alpha 또는 거래 가능성을 주장하지 않는다.

## 6. 사후 변경 금지

- 결과 확인 후 기간, 자산, 빈도, event quantile, null 구조, 반복 수, 최소 효과크기를 변경하지 않는다.
- 유의한 자산만 고르거나 특정 시간대·regime만 선택하지 않는다.
- L2 order book 연구는 본 결과와 독립된 새 protocol로만 수행한다.
- 뉴스·커뮤니티·sentiment를 사후 필터로 추가하지 않는다.

## 7. 한계

- 두 거래소의 residualization 모형은 동일하지만 체결 데이터 생성 규칙과 시장참여자는 다를 수 있다.
- 15분 집계는 초·밀리초 수준의 price discovery를 식별하지 못한다.
- 공통 뉴스와 글로벌 유동성 충격을 직접 관측하지 않으므로 동시 동조화만으로 모방을 구분할 수 없다.
- 7개 대형 survivor asset에 한정된다.
