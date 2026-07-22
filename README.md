# Crypto Order-Flow Synchronization

## 암호화폐의 매수·매도 압력은 시장 전체에서 함께 움직이는가?

이 저장소는 Binance와 OKX의 개별 체결자료를 이용해 여러 암호화폐의 공격적 주문흐름이
자산과 거래소 사이에서 동조화되는지 검정한 재현 가능한 미시구조 연구 프로젝트입니다.

가격 분산이나 음의 CSAD 계수를 herding으로 해석하지 않습니다. 각 자산의 가격변화,
시장수익률, 거래대금, 거래건수, 시간대와 요일을 development 표본에서 제거한 뒤 OOS
residual synchronization을 시간보존 null과 비교합니다.

## 주요 결과

| 연구 | 실제 | 시간 null | 판정 |
|---|---:|---:|---|
| Binance 7자산 평균 pairwise correlation | 0.14345 | 0.00114 | 지지 |
| Binance 6/7 방향 일치율 | 26.52% | 12.63% | 지지 |
| OKX 7자산 평균 pairwise correlation | 0.11857 | 0.00210 | 외부재현 |
| OKX 6/7 방향 일치율 | 24.22% | 12.80% | 외부재현 |
| Binance-OKX 동일 자산 평균 correlation | 0.27669 | 0.00097 | 지지 |
| 교차거래소 극단 흐름 방향 일치율 | 87.19% | 50.34% | 지지 |

반면 BTC·ETH→알트코인과 Binance↔OKX의 안정적인 15분 양의 전파는 사전 gate를
통과하지 못했습니다. 결과는 `market-wide order-flow synchronization`을 지지하지만
참여자 ID가 없으므로 intentional herding을 직접 식별하지 않으며, 미래수익률 alpha도
검정하지 않았습니다.

## L2 확장 상태

공식 [OKX Historical Market Data](https://www.okx.com/historical-data)의 400레벨 L2
archive를 대상으로 결과와 무관한 가용성 감사를 완료했습니다.
2024-04-08부터 2026-04-07까지 고정한 5개 날짜와 7개 자산의 35개 파일이 모두
확인됐습니다. ADA-USDT 하루 파일의 snapshot과 delta 1,987,014행을 전부 복원한 결과,
파싱 오류와 timestamp 역행은 0건이었고 1분 표본 1,440개에서 교차 호가는 없었습니다.

따라서 공통 spread 확대, depth 고갈, book imbalance가 주문흐름 동조화를 설명하는지
검정하는 180일 확인 연구를 사전등록했습니다. 7개 자산 압축 다운로드는 약 212GB로
추정되므로 날짜별 스트리밍 처리 후 원자료를 제거하고 feature만 보존합니다. 이 감사
통과는 데이터가 연구 가능하다는 뜻이며 L2 alpha가 확인됐다는 뜻은 아닙니다.

## 연구 설계

- 기간: 2024-04-08 포함, 2026-04-08 미포함
- 빈도: 15분
- 자산: BTC, ETH, XRP, SOL, DOGE, ADA, AVAX
- Development: 첫 1년
- OOS: 다음 1년
- Null: 반기 내 UTC 날짜 circular shift 499회
- 다중검정: 연구 family별 BH-FDR
- 방향성 비교: HAC regression과 UTC-day bootstrap 499회
- 제외 데이터: 뉴스, Reddit, Twitter/X, sentiment, 참여자 ID

## 설치

Python 3.11 이상을 권장합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## 공개 결과 검증

경량 결과와 null draw가 저장소에 포함되어 있으므로 원자료 없이 판정을 다시 계산할 수
있습니다.

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/verify_order_flow_synchronization.py
PYTHONPATH=src python scripts/verify_okx_order_flow_external_validation.py
PYTHONPATH=src python scripts/verify_cross_venue_order_flow_transmission.py
```

L2 감사는 공식 카탈로그를 다시 조회하고, 약 44MB인 ADA 하루 pilot archive를 내려받아
전체 book을 재구성합니다.

```bash
PYTHONPATH=src python scripts/run_okx_l2_availability_audit.py
```

봉인된 180일 L2 수집은 먼저 dry-run으로 작업 수를 확인한 뒤 실행합니다. 정상 cache가
있는 파일은 건너뛰므로 같은 명령으로 중단 지점부터 재개할 수 있습니다.

```bash
PYTHONPATH=src python scripts/collect_common_liquidity_l2.py
PYTHONPATH=src python scripts/collect_common_liquidity_l2.py --execute
PYTHONPATH=src python scripts/show_l2_collection_status.py
```

실행 중에는 하루·자산별 checksum, 품질 결과와 96개 15분 feature를 먼저 저장한 후 원본
archive를 삭제합니다. 컴퓨터가 꺼지면 자동 실행되지는 않지만, 두 번째 명령을 다시 실행하면
이미 검증된 cache 다음부터 계속됩니다.

장기 수집은 `ops/systemd/crypto-order-flow-l2-collector.service`를 user service로 등록할 수
있습니다. 등록 후에는 터미널을 닫아도 계속 실행되며 재부팅 후 네트워크가 연결되면
자동으로 남은 cache부터 재개합니다.

```bash
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/ops/systemd/crypto-order-flow-l2-collector.service" \
  ~/.config/systemd/user/crypto-order-flow-l2-collector.service
systemctl --user daemon-reload
systemctl --user enable --now crypto-order-flow-l2-collector.service
systemctl --user status crypto-order-flow-l2-collector.service
```

## 전체 재실행

대용량 원자료는 Git에 포함하지 않습니다. 필요한 로컬 경로와 입력 schema는
[`data/README.md`](data/README.md)를 참고하세요. 공개 결과를 덮어쓰지 않도록 재실행할
때는 config를 복사해 새로운 `output.base_dir`를 사용합니다.

```bash
PYTHONPATH=src python scripts/run_order_flow_synchronization.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_order_flow_futures_sensitivity.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_okx_order_flow_external_validation.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_cross_venue_order_flow_transmission.py --config YOUR_CONFIG.yaml
```

OKX runner는 공식 월별 tick archive를 재시작 가능하게 수집합니다. Binance primary는
준비된 15분 schema-v2 parquet와 public futures archive가 필요합니다.

## 폴더 구조

| 경로 | 내용 |
|---|---|
| `src/` | 잔차화, null, lead-lag, 외부검증, L2 복원 구현 |
| `scripts/` | 연구 실행기와 읽기 전용 verifier |
| `configs/research/` | 동결된 기간·자산·판정 기준 |
| `research_protocols/` | 결과 관찰 전 작성한 protocol과 seal |
| `outputs/v2/` | 보고서, 판정표, 경량 검증 산출물 |
| `tests/` | 핵심 통계·데이터 처리 단위 테스트 |

## 한계

- 두 중앙화 거래소와 7개 대형 survivor asset에 한정됩니다.
- AggTrades에는 계정·지갑, 주문 제출·취소, queue와 전체 L2 depth가 없습니다.
- 15분 자료는 초·밀리초 단위 price discovery 순서를 식별하지 못합니다.
- 동시 주문흐름은 미관측 공통정보나 글로벌 유동성 충격으로도 발생할 수 있습니다.
- 본 결과는 자동매매 성과나 거래 가능한 alpha의 증거가 아닙니다.

## License

코드와 저장소 문서는 [MIT License](LICENSE)로 배포됩니다. 외부 데이터에는 각 제공자의
별도 이용조건이 적용됩니다.
