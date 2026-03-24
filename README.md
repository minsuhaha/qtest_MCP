# qTest MCP (Xgen)

qTest와 연동되는 MCP(Model Context Protocol) 기반 테스트 자동화 프로젝트입니다.

## 프로젝트 구조

```
qTest MCP(Xgen)/
├── test_qtest_mcp_node.py   # qTest MCP 노드 테스트 스크립트
.github/
└── workflows/
    └── pr-webhook.yml       # PR 생성 시 n8n webhook 알림
```

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

```bash
python "qTest MCP(Xgen)/test_qtest_mcp_node.py"
```

## GitHub Actions

PR이 생성/업데이트되면 자동으로 n8n webhook으로 알림을 전송합니다.
