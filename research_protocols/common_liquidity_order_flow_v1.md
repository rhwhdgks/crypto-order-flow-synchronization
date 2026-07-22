# Common Liquidity and Order-Flow Synchronization Protocol v1

## 연구 질문

OKX 현물 7자산에서 확인된 동시 공격적 주문흐름 중 일부가 공통 L2 유동성 스트레스로
설명되는가? 이 연구는 참여자 모방을 식별하거나 미래수익률을 예측하는 연구가 아니다.

## 사전 조건

`okx_l2_availability_audit_v1`의 고정 품질 gate가 모두 통과한 경우에만 실행한다. 감사에서
연구 가설의 결과, 미래수익률 또는 아래 검정통계를 관찰하지 않았다.

## 표본

- 거래소와 시장: OKX spot
- 자산: BTC, ETH, XRP, SOL, DOGE, ADA, AVAX의 USDT pair
- L2: OKX 공식 400-level daily archive
- 기간: 2025-10-08 00:00 UTC 포함, 2026-04-06 00:00 UTC 미포함, 총 180일
- Development: 최초 60일
- OOS: 이후 120일
- 분석 빈도: 15분

날짜와 자산은 L2 결과를 보기 전에 고정했다. Development는 feature 변환, winsor 경계,
표준화 계수, 회귀계수와 event threshold에만 사용한다. 최종 판정은 OOS에서만 수행한다.

## L2 복원과 feature

각 UTC 날짜의 snapshot과 delta update를 순서대로 적용한다. size가 0인 level은 삭제한다.
각 UTC minute에서 해당 분 시작 이후 최초의 유효한 book을 표본으로 저장한다. 빈 book,
역행 timestamp, 교차 book과 날짜 경계 누락은 품질표에 기록한다.

자산별 15분 feature는 minute 표본의 median으로 정의한다.

- `spread_bps`: `(best ask - best bid) / midpoint × 10,000`
- `top10_depth`: ask와 bid 상위 10레벨의 quote notional 합
- `depth_depletion`: `-Δlog(top10_depth)`
- `abs_book_imbalance`: top-10 bid/ask quote depth imbalance의 절댓값

Development 1%와 99% 경계로 각 feature를 winsorize하고, development 평균과 표준편차로
자산별 표준화한다. 이 계수는 OOS에 그대로 적용한다. 공통 유동성 스트레스는 세
표준화 feature의 자산 횡단평균을 다시 development 기준으로 표준화한 값이다.

## Primary 검정

기존 `aggressor_residual_z`를 baseline 주문흐름으로 사용한다. 자산별로 development에서
다음 contemporaneous 회귀를 적합한다.

`flow_i,t = a_i + b1_i common_spread_t + b2_i common_depth_depletion_t + b3_i common_abs_imbalance_t + error_i,t`

계수를 OOS에 고정 적용해 liquidity-conditioned residual을 만든다. OOS 자산쌍 평균
correlation의 감소량을 다음처럼 정의한다.

`reduction = baseline mean pairwise correlation - conditioned mean pairwise correlation`

UTC 날짜 block bootstrap 499회로 감소량의 단측 p-value와 95% interval을 계산한다.
BH-FDR q-value가 0.05 이하이고 감소량이 0.02 이상이며 두 OOS 절반에서 감소량이 모두
양수일 때만 “공통 유동성 스트레스가 동조화의 일부를 설명한다”고 판정한다.

## Secondary 검정

Development에서 공통 주문흐름 절댓값과 공통 유동성 스트레스의 90 percentile을 각각
고정한다. OOS extreme-flow event가 동시에 liquidity-stress event인지 측정하고, OKX
liquidity 날짜를 같은 반기 안에서 최소 7일 circular shift한 null 499회와 비교한다.

BH-FDR q-value가 0.05 이하, 동시 event가 100건 이상, null 대비 risk ratio가 1.25
이상일 때 secondary gate를 통과한다. 이 검정은 primary 판정을 대체하지 않는다.

## 데이터 품질 제외 규칙

- 7개 자산의 완전한 15분 교집합만 사용한다.
- 초기 snapshot 이전 update, parse 오류, 역행 timestamp가 있는 날짜는 제외하고 사유를 공개한다.
- 자산별 사용 가능 날짜가 95% 미만이거나 OOS 날짜의 10% 이상이 제외되면 연구를 중단한다.
- 결과 확인 후 threshold, 기간, 자산, feature level 수를 변경하지 않는다.

## 해석 제한

통과하더라도 공통 유동성 상태와 체결 주문흐름의 동시 연관을 뜻할 뿐 인과관계, intentional
herding, 거래소 간 선도성 또는 미래수익률 alpha를 뜻하지 않는다. 뉴스, Reddit,
Twitter/X, sentiment와 참여자 ID는 사용하지 않는다.
