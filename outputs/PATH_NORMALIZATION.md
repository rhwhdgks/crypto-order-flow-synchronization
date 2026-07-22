# 공개 저장소 경로 정규화

연구 실행 시 생성된 source manifest의 로컬 절대경로를 공개 저장소에서 재현 가능한
상대경로(`./data/...`, `./outputs/...`)로 정규화했다.

- 통계량, 원자료 checksum, row count, protocol, config, seal과 판정은 변경하지 않았다.
- OKX `input_manifest.json`의 source-manifest 크기·SHA-256과 `provenance.json`의
  input-manifest SHA-256만 정규화된 파일에 맞게 갱신했다.
- 원자료는 저장소에 포함하지 않는다.
