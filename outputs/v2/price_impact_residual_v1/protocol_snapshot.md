# Price Impact Residual v1 사전등록

- 등록일: 2026-07-26 UTC
- 상태: OOS 결과 열람 전 동결
- 연구 성격: 1분 시장미시구조 기반 미래수익률 확인 연구

## 연구 질문

OKX 현물 7개 자산에서 공통 aggressor order flow가 강한 순간, 당시 유동성과 주문압력으로 예상되는 signed impact보다 덜 움직인 자산이 이후 15분 동안 catch-up하는가?

## 표본

- 자산: BTC, ETH, XRP, SOL, DOGE, ADA, AVAX의 USDT 현물
- 전체 기간: 2025-10-08 00:00 UTC 이상, 2026-04-06 00:00 UTC 미만
- Development: 최초 60일
- OOS: 이후 120일
- 빈도: 1분
- 한 시각에 최소 6개 자산이 정상이어야 한다.
- 뉴스, Reddit, Twitter/X, sentiment, 파생상품 데이터는 사용하지 않는다.

## Point-in-time 정의

분 `t` 시작 직후 복원된 L2 상태와 분 `t` 동안 발생한 체결만 feature로 사용한다.
분 `t`의 observed midpoint return은 `t`와 `t+1` 시작 midpoint로 계산한다.
따라서 impact-gap과 포트폴리오 결정은 `t+1`에만 가능하며, 진입가격도 `t+1` L2에서 계산한다.
무체결 분은 flow를 0으로 두되 L2 midpoint를 유지한다.

## 예상 가격충격

Development에서 다음 pooled OLS를 한 번 적합하고 모든 계수를 OOS에 고정한다.

```text
target = sign(common_flow_t) * log(midpoint_{i,t+1} / midpoint_{i,t})

predictors =
  abs(common_flow_t)
  sign(common_flow_t) * local_flow_{i,t}
  spread_{i,t}
  log(top10_depth_{i,t})
  sign(common_flow_t) * book_imbalance_{i,t}
  UTC hour sin/cos
  asset fixed effects
```

연속형 predictor의 0.5%/99.5% winsor 경계와 평균·표준편차는 Development에서만 정하고 OOS에 고정한다.

```text
impact_gap = observed_signed_impact - expected_signed_impact
```

## 사건과 포트폴리오

- Development의 `abs(common_flow)` 95백분위수를 고정 event threshold로 사용한다.
- 각 event에서 impact-gap이 가장 낮은 2개를 underreactor, 가장 높은 2개를 overreactor로 선택한다.
- Primary는 catch-up이다.
- 공통 매수압력: underreactor 매수, overreactor 매도
- 공통 매도압력: underreactor 매도, overreactor 매수
- reversal은 위 포지션의 정확한 반대이며 falsification 결과로만 보고한다.
- 같은 event에서 4개 포지션을 동일 명목금액으로 구성한다.

## 실행과 결과

- horizon: 1, 5, 15, 30분
- primary horizon: 15분
- 고정 비용: round-trip 4/8/12 bps
- primary 비용: 8 bps
- capacity: 자산별 10,000/50,000/100,000 USDT
- primary capacity: 자산별 10,000 USDT
- 분별 top-10 book을 실제로 걸어 entry/exit VWAP를 계산한다.
- top-10에서 목표 명목금액을 전부 채울 수 없는 event는 해당 capacity 결과에서 제외 사유를 기록한다.
- primary non-overlap은 직전 선택 event로부터 15분 이상 지난 첫 event만 남긴다.
- midpoint gross, 고정 비용 net, top-10 sweep net을 모두 분리 보고한다.

## 통계와 통과 기준

- UTC 날짜 block bootstrap 499회
- 4개 horizon family에 BH-FDR 5% 적용
- primary 판정은 다음을 모두 만족해야 한다.

1. 15분 비중첩 OOS event가 100건 이상
2. 8bps 차감 평균과 중앙값이 모두 양수
3. OOS 전반부와 후반부 평균이 모두 양수
4. 7개 중 최소 4개 자산의 포지션 기여 평균이 양수
5. 자산당 10,000 USDT top-10 full-fill 표본에서도 평균이 양수
6. 날짜 block bootstrap 단측 p-value의 BH-FDR q-value가 0.05 이하

기준은 결과 확인 후 낮추지 않는다. 실패 시 동일 OOS에서 percentile, 자산 수, 방향, horizon, 비용 또는 capacity를 재선택하지 않는다.

## 해석 제한

통과해도 제한된 OKX 표본의 가격충격 잔차 catch-up으로 해석한다.
참여자의 모방, 구조적 인과관계, 다른 거래소 일반화 또는 실거래 수익을 자동으로 뜻하지 않는다.
실거래 전에는 독립 기간 또는 독립 거래소의 재현 검정이 추가로 필요하다.
