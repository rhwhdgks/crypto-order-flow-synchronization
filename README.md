# 코인 주문흐름 동조화 연구

## 암호화폐의 매수·매도 압력은 시장 전체에서 함께 움직이는가?

바이낸스와 OKX의 개별 체결자료를 이용해 여러 코인의 매수·매도 압력이 같은 시간에 움직이는지 조사했습니다. 가격 변화, 거래량, 시간대 등의 영향을 걷어낸 뒤, 연구에 쓰지 않은 기간과 다른 거래소에서도 같은 결과가 나오는지 확인했습니다.

체결자료에는 누가 거래했는지가 없어 이 결과만으로 투자자들이 의도적으로 서로 따라 거래했다고 말할 수는 없습니다. 자세한 변수 처리와 표본 구분은 아래 [연구 설계](#연구-설계)에 정리했습니다.

## 먼저 읽을 결론

- 7개 코인의 매수·매도 압력이 함께 움직이는 현상은 바이낸스의 새 기간과 OKX 자료에서도 관찰됐습니다.
- 현재 호가창 상태만으로 그 현상을 설명하거나, 15분 뒤 주문흐름·호가창 상태를 예측하려는 가설은 사전에 정한 기준을 통과하지 못했습니다.
- 함께 움직인다는 사실 자체는 미래 수익률을 검정한 결과가 아닙니다. 별도로 시험한 가격 따라잡기 전략도 거래비용을 고려하면 수익성이 확인되지 않았습니다.

공개된 결과표와 검증 스크립트는 [공개 결과 검증](#공개-결과-검증)에서 확인할 수 있습니다. 원자료부터 전체 연구를 다시 실행하려면 별도 데이터가 필요합니다.

## 주요 결과

동조화 행의 비교값은 자료의 날짜를 옮겨 **우연히 동시에 움직인 경우**를 흉내 낸 결과입니다. 마지막 네 행에는 연구 전에 정한 최소 효과나 통계 판정값을 적었습니다. 계산 방법은 [연구 설계](#연구-설계)와 공개 결과 파일에서 확인할 수 있습니다.

| 확인한 내용 | 실제 자료 | 비교값·판정 기준 | 판정 |
|---|---:|---:|---|
| 바이낸스 7개 코인 간 주문흐름 상관 | 0.14345 | 0.00114 | 동조화 관찰 |
| 바이낸스에서 7개 중 6개 코인의 방향이 같은 비율 | 26.52% | 12.63% | 동조화 관찰 |
| OKX 7개 코인 간 주문흐름 상관 | 0.11857 | 0.00210 | 다른 거래소에서 재확인 |
| OKX에서 7개 중 6개 코인의 방향이 같은 비율 | 24.22% | 12.80% | 다른 거래소에서 재확인 |
| 두 거래소의 같은 코인 간 주문흐름 상관 | 0.27669 | 0.00097 | 동조화 관찰 |
| 두 거래소에서 주문흐름이 크게 쏠릴 때 방향 일치율 | 87.19% | 50.34% | 동조화 관찰 |
| 호가창 상태를 반영한 뒤 코인 간 상관 감소 | 0.00004 | 사전 기준 0.02000 | 설명 가설 미지지 |
| 극단적 주문흐름과 호가창 불안정의 동시 발생이 우연보다 잦은 정도 | 0.935 | 사전 기준 1.25 | 설명 가설 미지지 |
| 호가창으로 15분 뒤 주문흐름을 예측할 때 오차 개선 | -1.25% | 사전 기준 +1.00% | 예측 가설 미지지 |
| 주문흐름으로 15분 뒤 호가창을 예측할 때 오차 개선 | +0.09% | 보정된 유의확률 0.972 | 예측 가설 미지지 |

비트코인·이더리움에서 다른 코인으로, 또는 바이낸스에서 OKX로 주문흐름이 15분 뒤까지 이어진다는 가설은 사전에 정한 기준을 통과하지 못했습니다. 여러 코인의 주문흐름이 같은 시점에 움직인다는 결과를 투자자의 의도적인 집단추종이나 미래 수익률 예측으로 해석할 수는 없습니다.

## 호가창 자료를 추가로 확인한 결과

공식 [OKX Historical Market Data](https://www.okx.com/historical-data)의 400레벨 L2
archive를 대상으로 결과와 무관한 가용성 감사를 완료했습니다.
2024-04-08부터 2026-04-07까지 고정한 5개 날짜와 7개 자산의 35개 파일이 모두
확인됐습니다. ADA-USDT 하루 파일의 snapshot과 delta 1,987,014행을 전부 복원한 결과,
파싱 오류와 timestamp 역행은 0건이었고 1분 표본 1,440개에서 교차 호가는 없었습니다.

이후 180일·7자산 자료 1,260개를 날짜별로 처리해 7자산 완전 교집합 173일을 확보했고,
봉인된 확인 연구를 완료했습니다. 공통 spread, top-10 depth depletion, absolute book
imbalance를 development에서 적합해 OOS 주문흐름에서 제거했지만 평균 자산쌍 상관은
0.12973에서 0.12969로만 감소했습니다. 감소량 0.00004는 사전 최소효과 0.02000에 크게
못 미쳤고 두 OOS 절반에서도 안정적이지 않았습니다.

극단 주문흐름과 L2 stress의 overlap도 shift-null보다 높지 않았습니다. 따라서 단순한
contemporaneous L2 유동성 상태가 기존 주문흐름 동조화를 설명한다는 가설은 지지되지
않습니다. 미래수익률을 사용하지 않았으므로 L2 alpha에 대한 판정은 아닙니다.

별도 봉인한 동적 연구에서도 현재 L2 상태를 추가한 모델은 15분, 30분, 60분 뒤 공통
주문흐름 크기의 OOS MSE를 각각 1.25%, 1.72%, 1.65% 악화시켰습니다. 반대 방향인
주문흐름→L2도 최대 개선이 0.09%였고 BH-FDR을 통과하지 못했습니다. 따라서 단순 L2
유동성의 동시 설명과 1시간 이내 예측 가설은 모두 지지되지 않습니다.

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
PYTHONPATH=src python scripts/verify_common_liquidity_order_flow.py
PYTHONPATH=src python scripts/verify_dynamic_liquidity_flow.py
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

## 1분 가격충격 잔차 연구

공통 주문압력 대비 덜 움직인 자산의 15분 catch-up을 검정하는 별도 protocol을 봉인했다.
1분 L2·aggressor flow와 10k/50k/100k USDT top-10 sweep 실행가격을 사용하며,
Development 60일에서 모델과 95백분위 event threshold를 고정하고 OOS 120일에서만
판정한다. Primary는 15분, 8bps, 자산당 10,000 USDT이며 동일 OOS에서 조건을 재선택하지
않는다.

수집과 OOS 분석은 완료됐다. Primary 8,918건의 평균 순수익률은 -0.1052%,
중앙값은 -0.1041%였고 모든 confirmatory gate가 실패해 catch-up alpha는 지지되지
않았다. 상세 결과는
[`price_impact_residual_report.md`](outputs/v2/price_impact_residual_v1/price_impact_residual_report.md)에
있다.

```bash
PYTHONPATH=src python scripts/show_price_impact_collection_status.py
systemctl --user status crypto-price-impact-collector.service
journalctl --user -u crypto-price-impact-collector.service -f
```

신규 표본으로 수집 service를 실행하면 정상 완료 후
`crypto-price-impact-analysis.service`가 봉인된 OOS 분석과 verifier를 자동 실행한다.

## 전체 재실행

대용량 원자료는 Git에 포함하지 않습니다. 필요한 로컬 경로와 입력 schema는
[`data/README.md`](data/README.md)를 참고하세요. 공개 결과를 덮어쓰지 않도록 재실행할
때는 config를 복사해 새로운 `output.base_dir`를 사용합니다.

```bash
PYTHONPATH=src python scripts/run_order_flow_synchronization.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_order_flow_futures_sensitivity.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_okx_order_flow_external_validation.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_cross_venue_order_flow_transmission.py --config YOUR_CONFIG.yaml
PYTHONPATH=src python scripts/run_common_liquidity_order_flow.py
PYTHONPATH=src python scripts/run_dynamic_liquidity_flow.py
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
