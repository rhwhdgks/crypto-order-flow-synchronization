# OKX Order-Flow External Validation v1 Composite Compatibility Amendment 2

- 기록 시각: 2026-07-21
- 직전 seal 파일: `okx_order_flow_external_validation_v1.pre_composite.seal.json`
- 결과 관찰 여부: synchronization 함수가 메모리에서 호출됐지만 통계량이 파일·로그·표준출력에 저장·출력되지 않았고 연구자가 수치를 관찰하지 않음

## 실패 원인

OKX 원천 175개 파일의 수집·15분 집계·100% coverage 품질 gate 통과 후, Binance runner와 공유한 `build_flow_composites` 함수가 OKX 설정에 없는 `futures_symbols`를 필수로 조회해 `KeyError`로 중단됐다. OKX 프로토콜은 spot major·alt composite만 필요하고 futures composite를 정의하지 않는다.

## 변경

- `build_flow_composites`는 `futures_symbols`가 설정에 있을 때만 `spot_futures5_flow` 열을 생성한다.
- major·alt composite 산식과 development 표준화는 변경하지 않는다.
- Binance v1은 `futures_symbols`를 계속 제공하므로 기존 futures confirmation 동작이 변하지 않는다.

## 변경하지 않는 항목

- 데이터, 자산, 기간, 빈도, residual controls
- synchronization 지표·null·seed·BH-FDR·최소효과·gate
- lead-lag·bootstrap 설계와 판정 gate
- 뉴스·Reddit·Twitter/X·sentiment·alpha 제외

이 amendment는 통계 설계를 변경하지 않고 선택적 futures composite를 외부검증 runner에서 생략하는 호환성 교정이다.
