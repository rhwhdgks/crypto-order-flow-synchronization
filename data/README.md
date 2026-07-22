# 로컬 데이터 안내

대용량 체결·선물 원자료는 Git에 포함하지 않는다. 아래 경로는 config에서 변경할 수 있다.

## Binance spot primary

기본 config 입력:

`outputs/v2/tick/semantic_validation/raw_2y/intermediate/tick_micro_frame_15m.parquet`

필수 컬럼:

- `bucket_start`: UTC 15분 시작시각
- `symbol`
- `interval_minutes`: 15
- `schema_version`: 2
- `bucket_return`
- `total_quote_quantity`
- `transaction_count`
- `aggressor_imbalance`

기간은 2024-04-08 00:00 UTC 이상, 2026-04-08 00:00 UTC 미만이고 7자산 완전
교집합 490,560행이어야 한다. Binance public aggTrades의 buyer-maker flag로 aggressor
side를 계산하며 미래 bucket을 사용하지 않는다.

## Binance futures confirmation

기본 경로는 `data/futures_archive/`다. Binance public futures metrics의 월별 파일을
심볼별로 배치한다. config의 5개 공통자산과 정확한 2년 구간을 사용한다.

## OKX external validation

기본 경로는 `data/okx_tick_archive/`다. OKX runner가 공식 월별 tick ZIP을 자동으로
수집하고 중단 시 이어받는다. 전체 실행에는 약 14GB의 로컬 공간이 필요하다.

## Cross-venue study

Binance와 OKX runner가 각각 생성한 다음 파일을 입력으로 사용한다.

- `outputs/v2/order_flow_synchronization_v1/intermediate/aggressor_residual_panel.parquet`
- `outputs/v2/okx_order_flow_external_validation_v1/intermediate/aggressor_residual_panel.parquet`

공개 결과 검증만 수행할 때는 이 원자료와 intermediate 파일이 필요하지 않다.
