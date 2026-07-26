# 프로젝트 지침

## 목표

암호화폐의 공격적 주문흐름이 자산·거래소 사이에서 동조화되는지 재현 가능하게 검정한다.
현재 범위는 시장 미시구조 연구이며 자동매매 시스템이 아니다.

## 고정 해석

- `market-wide order-flow synchronization`과 intentional herding을 구분한다.
- 참여자 ID 없이 누가 누구를 모방했는지 주장하지 않는다.
- 미래수익률을 검정하지 않은 결과를 alpha로 표현하지 않는다.
- 유의성뿐 아니라 사전 최소 효과크기, 기간 안정성, null 비교를 함께 적용한다.
- Binance와 OKX의 거래소 내부 동조화는 지지된다.
- Binance-OKX 교차거래소 동시 동조화는 지지된다.
- 자산군 cascade와 거래소 간 안정적인 양의 전파는 지지되지 않는다.
- 단순한 contemporaneous L2 spread, top-10 depth depletion, absolute book imbalance가
  주문흐름 동조화를 실질적으로 설명한다는 가설은 OOS에서 지지되지 않는다.
- L2 상태의 15·30·60분 주문흐름 크기 예측과 역방향 예측도 OOS에서 지지되지 않는다.

## 연구 규칙

- 결과 확인 전 protocol, config, seal을 작성한다.
- OOS 결과를 본 뒤 자산, 기간, horizon, threshold를 최적화하지 않는다.
- Development에서 적합한 residualization 계수와 scaler를 OOS에 고정한다.
- 미래정보 누수와 결과 덮어쓰기를 금지한다.
- 뉴스, Reddit, Twitter/X, sentiment를 사후 필터로 추가하지 않는다.
- 원자료와 자격증명은 Git에 올리지 않는다.

## 다음 연구

`common_liquidity_order_flow_v1`과 `dynamic_liquidity_flow_v1`은 모두 완료됐으며
confirmatory gate를 통과하지 못했다. 동일 표본에서 L2 level, threshold, 기간, horizon을
바꿔 유의한 조합을 찾지 않는다. 다음 연구는 새 protocol을 봉인한 뒤에만 진행하며
derivatives liquidity, 독립 표본 또는 식별 가정이 다른 공통정보 proxy를 검정한다.

`price_impact_residual_v1`은 결과 열람 전 봉인됐고 1분 L2·flow 수집 중이다. 수집 완료
전 OOS 결과를 계산하지 않으며, 완료 후에도 봉인된 15분·8bps·10,000 USDT primary gate를
그대로 적용한다.
