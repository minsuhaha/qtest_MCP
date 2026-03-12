"""
qTest MCP Node - Xgen Workflow Node for qTest Test Management Integration

제공 도구 (28개):
  - 테스트 케이스 생성/조회/수정/승인 (기본, 스텝포함, 자동생성)
  - 프로젝트 / 요구사항 / 모듈 관리
  - Test Execution (Cycle / Suite / Run / Log)
  - 자동화 호스트 / 에이전트 / 스케줄 관리
  - 통합 검색 / 결함 등록

Architecture:
  - Node 추상 클래스 상속 (editor.node_composer)
  - LangChain StructuredTool 로 각 도구 정의
  - execute() 에서 List[StructuredTool] 포함한 dict 반환
  - 인증: Token 우선, 없으면 Username/Password 로그인

Author: Converted from qtest-mcp_v5.py for Xgen Node compatibility
"""

import base64
import datetime
import json
import logging
import re
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field

from editor.node_composer import Node
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Schemas (Tool Parameter Definitions)
# ============================================================================

# ---------- 테스트 케이스 생성 ----------

class CreateTestCaseSchema(BaseModel):
    name: str = Field(description="테스트 케이스 이름")
    description: Optional[str] = Field(default="", description="테스트 케이스 설명")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class CreateTestCaseWithStepsSchema(BaseModel):
    name: str = Field(description="테스트 케이스 이름")
    description: Optional[str] = Field(default="", description="테스트 케이스 설명")
    test_steps: Optional[List[dict]] = Field(
        default=None,
        description='테스트 스텝 배열. 각 스텝: {"description": "...", "expected": "..."}. 미제공 시 description에서 자동 생성'
    )
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class GenerateAndCreateSchema(BaseModel):
    name: str = Field(description="테스트 케이스 이름")
    content: str = Field(description="테스트 케이스 내용/설명. 이 내용을 기반으로 테스트 스텝이 자동 생성됩니다.")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


# ---------- 프로젝트 ----------

class ListProjectsSchema(BaseModel):
    page: Optional[int] = Field(default=1, description="페이지 번호 (1부터 시작)")
    page_size: Optional[int] = Field(default=25, description="페이지당 항목 수 (최대 100)")


class GetProjectSchema(BaseModel):
    project_id: str = Field(description="프로젝트 ID")


# ---------- 요구사항 ----------

class ListRequirementsSchema(BaseModel):
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    parent_id: Optional[int] = Field(default=None, description="상위 모듈 ID")
    page: Optional[int] = Field(default=1, description="페이지 번호")
    size: Optional[int] = Field(default=25, description="페이지당 항목 수 (최대 100)")


class GetRequirementSchema(BaseModel):
    requirement_id: int = Field(description="요구사항 ID")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class ListTestcasesForRequirementsSchema(BaseModel):
    requirement_ids: List[int] = Field(description="요구사항 ID 배열 (하나 이상)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class LinkTestcasesToRequirementSchema(BaseModel):
    requirement_id: int = Field(description="요구사항 ID")
    testcase_ids: List[int] = Field(description="연결할 테스트 케이스 ID 배열")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


# ---------- 모듈 ----------

class SearchModulesSchema(BaseModel):
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    search: Optional[str] = Field(default=None, description="검색할 모듈 이름")
    parent_id: Optional[int] = Field(default=None, description="상위 모듈 ID")


class CreateModuleSchema(BaseModel):
    name: str = Field(description="모듈 이름")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    description: Optional[str] = Field(default="", description="모듈 설명")
    parent_id: Optional[int] = Field(default=None, description="상위 모듈 ID")


# ---------- 테스트 케이스 CRUD ----------

class ListTestcasesSchema(BaseModel):
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    parent_id: Optional[int] = Field(default=None, description="모듈 ID")
    page: Optional[int] = Field(default=1, description="페이지 번호")
    size: Optional[int] = Field(default=25, description="페이지당 항목 수 (최대 100)")


class GetTestcaseSchema(BaseModel):
    testcase_id: int = Field(description="테스트 케이스 ID")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    expand: Optional[str] = Field(default=None, description="'teststep' 입력 시 테스트 스텝도 함께 조회")
    version_id: Optional[int] = Field(default=None, description="테스트 케이스 버전 ID")


class UpdateTestcaseSchema(BaseModel):
    testcase_id: int = Field(description="테스트 케이스 ID")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    name: Optional[str] = Field(default=None, description="수정할 이름")
    description: Optional[str] = Field(default=None, description="수정할 설명")
    precondition: Optional[str] = Field(default=None, description="수정할 사전조건")
    parent_id: Optional[int] = Field(default=None, description="이동할 모듈 ID")
    test_steps: Optional[List[dict]] = Field(
        default=None,
        description='수정할 테스트 스텝 배열. 각 스텝: {"description": "...", "expected": "..."}'
    )
    automation_content: Optional[str] = Field(
        default=None,
        description="Automation Content 값 (예: 'test_module#test_method'). 자동화 테스트 매핑에 사용됩니다."
    )


class UpdateTestcasePropertySchema(BaseModel):
    testcase_id: int = Field(description="테스트 케이스 ID")
    field_name: str = Field(description="변경할 Property 이름 (예: 'Automation Content', 'Priority', 'Automation')")
    field_value: str = Field(description="변경할 값 (예: 'test_module#test_method', '723' 등)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


# ---------- 테스트 런 / 로그 ----------

class ListTestrunsForTestcasesSchema(BaseModel):
    testcase_ids: List[int] = Field(description="테스트 케이스 ID 배열")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class ListTestlogsForTestrunSchema(BaseModel):
    test_run_id: int = Field(description="테스트 런 ID")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    page: Optional[int] = Field(default=1, description="페이지 번호")
    page_size: Optional[int] = Field(default=25, description="페이지당 항목 수 (최대 100)")


# ---------- 결함 ----------

class CreateDefectSchema(BaseModel):
    test_log_id: int = Field(description="테스트 로그 ID")
    summary: str = Field(description="결함 요약")
    description: str = Field(description="결함 상세 설명 (실제 결과 vs 예상 결과)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


# ---------- 통합 검색 ----------

class SearchObjectsSchema(BaseModel):
    object_type: str = Field(
        description="검색할 객체 유형: releases, requirements, test-cases, test-runs, test-suites, test-cycles, test-logs, builds, defects"
    )
    query: str = Field(description="검색 쿼리")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    search_field: Optional[str] = Field(default="name", description="검색 대상 필드: 'id' 또는 'name' (기본: 'name')")
    exact_match: Optional[bool] = Field(default=False, description="정확히 일치 검색 여부 (기본: False)")
    page: Optional[int] = Field(default=1, description="페이지 번호")
    size: Optional[int] = Field(default=25, description="페이지당 항목 수")


# ---------- TC 승인 ----------

class ApproveTestCaseSchema(BaseModel):
    testcase_id: int = Field(description="승인할 테스트 케이스 ID")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


class ApproveTestCasesBulkSchema(BaseModel):
    testcase_ids: List[int] = Field(description="승인할 테스트 케이스 ID 배열")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")


# ---------- Test Execution ----------

class CreateTestCycleSchema(BaseModel):
    name: str = Field(description="Test Cycle 이름")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    description: Optional[str] = Field(default="", description="Test Cycle 설명")
    parent_id: Optional[int] = Field(default=0, description="상위 Release 또는 Test Cycle ID. 0이면 루트(root)에 생성")
    parent_type: Optional[str] = Field(default="root", description="상위 컨테이너 유형: 'root', 'release', 'test-cycle'")


class CreateTestSuiteSchema(BaseModel):
    name: str = Field(description="Test Suite 이름")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    description: Optional[str] = Field(default="", description="Test Suite 설명")
    parent_id: Optional[int] = Field(default=0, description="상위 Release 또는 Test Cycle ID. 0이면 루트(root)에 생성")
    parent_type: Optional[str] = Field(default="root", description="상위 컨테이너 유형: 'root', 'release', 'test-cycle'")


class CreateTestRunSchema(BaseModel):
    name: str = Field(description="Test Run 이름")
    test_case_id: int = Field(description="연결할 테스트 케이스 ID (승인된 TC여야 함)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    description: Optional[str] = Field(default="", description="Test Run 설명")
    parent_id: Optional[int] = Field(default=0, description="상위 Test Suite, Test Cycle 또는 Release ID. 0이면 루트(root)에 생성")
    parent_type: Optional[str] = Field(default="root", description="상위 컨테이너 유형: 'root', 'test-suite', 'test-cycle', 'release'")
    test_case_version_id: Optional[int] = Field(default=None, description="테스트 케이스 버전 ID (미지정 시 최신 승인 버전 사용)")


class CreateTestRunsBulkSchema(BaseModel):
    test_case_ids: List[int] = Field(description="Test Run을 생성할 테스트 케이스 ID 배열 (승인된 TC여야 함)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    parent_id: Optional[int] = Field(default=0, description="상위 Test Suite, Test Cycle 또는 Release ID. 0이면 루트(root)에 생성")
    parent_type: Optional[str] = Field(default="test-suite", description="상위 컨테이너 유형: 'root', 'test-suite', 'test-cycle', 'release'")


# ---------- Automation ----------

class SearchAutomationAgentsSchema(BaseModel):
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    host_id: Optional[int] = Field(default=None, description="특정 Host ID로 필터링 (선택사항)")
    agent_name: Optional[str] = Field(default=None, description="Agent 이름으로 필터링 (선택사항)")
    framework: Optional[str] = Field(default=None, description="프레임워크로 필터링 (예: 'testNG', 'junit', 'universalAgent')")
    active_only: Optional[bool] = Field(default=True, description="활성 Agent만 조회 (기본: True)")


class CreateAutomationScheduleSchema(BaseModel):
    test_run_ids: List[int] = Field(description="실행할 Test Run ID 배열")
    agent_id: int = Field(description="실행할 Automation Agent ID (agent_server_id)")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    host_id: Optional[int] = Field(default=None, description="Automation Host ID (host_server_id, 선택사항)")


class SubmitAutoTestLogSchema(BaseModel):
    test_run_id: int = Field(description="테스트 런 ID")
    status: str = Field(description="실행 결과 상태 (예: 'PASSED', 'FAILED', 'INCOMPLETE', 'BLOCKED')")
    project_id: Optional[str] = Field(default=None, description="프로젝트 ID (미지정 시 기본값 사용)")
    name: Optional[str] = Field(default=None, description="테스트 로그 이름 (미지정 시 Test Run 이름 사용)")
    note: Optional[str] = Field(default="", description="실행 메모/노트")
    automation_content: Optional[str] = Field(default="", description="Automation Content 값")
    test_step_logs: Optional[List[dict]] = Field(
        default=None,
        description='테스트 스텝별 결과 배열. 각 스텝: {"description": "...", "expected_result": "...", "actual_result": "...", "status": "..."}'
    )


# ============================================================================
# qTest Synchronous HTTP Client
# ============================================================================

class QTestClient:
    """동기(Synchronous) qTest API 클라이언트.
    """

    def __init__(self, qtest_url: str, token: str, default_project_id: str = ""):
        self.qtest_url = qtest_url.rstrip("/")
        self.token = token
        self.default_project_id = default_project_id

    def _get_headers(self) -> dict:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }

    def resolve_project_id(self, project_id: Optional[str] = None) -> str:
        return project_id or self.default_project_id

    def request(self, method: str, path: str, payload=None, params: dict = None, timeout: float = 30.0) -> dict:
        """qTest REST API 호출 (동기). 결과는 dict 로 반환하며 오류 시 {"error": ...} 반환."""
        url = f"{self.qtest_url}{path}"
        headers = self._get_headers()
        try:
            with httpx.Client(timeout=timeout) as client:
                if method.upper() == "GET":
                    response = client.get(url, headers=headers, params=params)
                elif method.upper() == "POST":
                    if payload is not None:
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        response = client.post(url, headers=headers, content=body, params=params)
                    else:
                        response = client.post(url, headers=headers, params=params)
                elif method.upper() == "PUT":
                    if payload is not None:
                        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        response = client.put(url, headers=headers, content=body, params=params)
                    else:
                        response = client.put(url, headers=headers, params=params)
                else:
                    return {"error": f"지원되지 않는 HTTP 메서드: {method}"}

                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return {"success": True}
                return response.json()

        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            try:
                error_data = e.response.json()
                if isinstance(error_data, dict):
                    error_msg = error_data.get("message", error_data.get("error", {}).get("message", error_msg))
            except Exception:
                pass
            return {"error": f"API 요청 실패 ({e.response.status_code}): {error_msg}"}
        except Exception as e:
            return {"error": f"오류 발생: {str(e)}"}


# ============================================================================
# 헬퍼: 설명 텍스트 → 테스트 스텝 자동 생성
# ============================================================================

def _generate_test_steps(description: str) -> List[dict]:
    """번호/불릿 목록 또는 문장 단위로 테스트 스텝을 자동 생성합니다."""
    test_steps = []
    if not description:
        return test_steps

    lines = description.split("\n")
    numbered_pattern = r"^\s*(\d+)[\.\)]\s*(.+)$"
    bullet_pattern = r"^\s*[-•]\s*(.+)$"

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = re.match(numbered_pattern, line)
        if match:
            step_text = match.group(2).strip()
            if ":" in step_text or "->" in step_text or "→" in step_text:
                parts = re.split(r"[:->→]", step_text, 1)
                step_desc = parts[0].strip()
                expected = parts[1].strip() if len(parts) > 1 else "정상적으로 동작한다"
            else:
                step_desc, expected = step_text, "정상적으로 동작한다"
            test_steps.append({"description": step_desc, "expected": expected})
            continue

        match = re.match(bullet_pattern, line)
        if match:
            step_text = match.group(1).strip()
            if ":" in step_text or "->" in step_text or "→" in step_text:
                parts = re.split(r"[:->→]", step_text, 1)
                step_desc = parts[0].strip()
                expected = parts[1].strip() if len(parts) > 1 else "정상적으로 동작한다"
            else:
                step_desc, expected = step_text, "정상적으로 동작한다"
            test_steps.append({"description": step_desc, "expected": expected})

    if not test_steps:
        sentences = [s.strip() for s in description.replace("\n", " ").split(".") if len(s.strip()) > 5]
        for sentence in sentences:
            step_desc, expected = sentence, "정상적으로 동작한다"
            if "하면" in sentence:
                parts = sentence.split("하면", 1)
                if len(parts) == 2:
                    step_desc, expected = parts[0].strip(), parts[1].strip()
            test_steps.append({"description": step_desc, "expected": expected})

    if not test_steps:
        test_steps.append({"description": description, "expected": "기대한 결과가 나타난다"})

    return test_steps


# ============================================================================
# qTest MCP Node
# ============================================================================

class QTestMCP(Node):
    """qTest MCP Node - qTest 테스트 관리 플랫폼 연동 도구 제공 노드.

    28개의 qTest 도구를 StructuredTool 형태로 반환합니다.
    출력된 도구들은 다운스트림 Agent 노드와 연결하여 사용합니다.
    """

    # NOTE: categoryId / functionId 는 시스템의 CATEGORIES_LABEL_MAP / FUNCTION_LABEL_MAP
    #       에 등록된 유효한 값으로 변경이 필요합니다.
    categoryId = "xgen"
    functionId = "file_system"
    nodeId = "integration/qtest_mcp"
    nodeName = "qTest MCP"
    description = (
        "qTest 테스트 관리 플랫폼 MCP - 테스트 케이스 관리, Test Execution (Cycle/Suite/Run), "
        "요구사항 연결, 자동화 스케줄링, 결함 등록 등 28개의 도구를 제공합니다."
    )
    tags = ["mcp", "qtest", "test", "qa", "test-management", "automation", "integration"]

    inputs = []
    outputs = [
        {"id": "qtest_tools", "name": "QTestTools", "type": "TOOL"},
    ]

    parameters = [
        {
            "id": "qtest_url",
            "name": "qTest URL",
            "type": "STR",
            "value": "https://plateer.qtestnet.com",
            "required": True,
            "description": "qTest 서버 URL (예: https://yourcompany.qtestnet.com)",
        },
        {
            "id": "qtest_token",
            "name": "qTest Token",
            "type": "STR",
            "value": "cGxhdGVlcnxyeXVzdW5nZEBwbGF0ZWVyLmNvbToxODAzMDE0MjMwNTYzOjUxMjQ5OGQ3NGE1OWE1YjJlZjYxOWUyYmFjODVkOTRi",
            "required": False,
            "description": "qTest 로그인 세션 토큰. Username/Password 미사용 시 이 값이 필요합니다.",
        },
        {
            "id": "qtest_username",
            "name": "qTest Username",
            "type": "STR",
            "value": "",
            "required": False,
            "optional": True,
            "description": "qTest 로그인 이메일. Token 미사용 시 Password 와 함께 필요합니다.",
        },
        {
            "id": "qtest_password",
            "name": "qTest Password",
            "type": "STR",
            "value": "",
            "required": False,
            "optional": True,
            "description": "qTest 로그인 비밀번호. Token 미사용 시 Username 과 함께 필요합니다.",
        },
        {
            "id": "default_project_id",
            "name": "Default Project ID",
            "type": "STR",
            "value": "127369",
            "required": False,
            "description": "기본 qTest 프로젝트 ID. 도구 호출 시 project_id 미지정 시 이 값이 사용됩니다.",
        },
    ]

    # -------------------------------------------------------------------------
    # 인증
    # -------------------------------------------------------------------------

    @staticmethod
    def _login(qtest_url: str, username: str, password: str) -> str:
        """Username/Password 로 qTest 에 로그인하여 Bearer 토큰을 반환합니다."""
        site_name = qtest_url.replace("https://", "").replace("http://", "").split(".")[0]
        basic_auth = base64.b64encode(f"{site_name}:".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = f"grant_type=password&username={username}&password={password}"
        with httpx.Client(timeout=30.0) as client:
            response = client.post(f"{qtest_url}/oauth/token", headers=headers, content=body)
            response.raise_for_status()
            data = response.json()
        return f"bearer {data['access_token']}"

    def _get_auth_token(self, qtest_url: str, qtest_token: str, username: str, password: str) -> str:
        """인증 토큰 반환. Token 이 있으면 그대로 사용, 없으면 Username/Password 로 로그인."""
        if qtest_token:
            return qtest_token
        if username and password:
            return self._login(qtest_url, username, password)
        raise ValueError(
            "인증 정보가 없습니다. 파라미터에 qtest_token 또는 (qtest_username + qtest_password)를 설정해주세요."
        )

    # -------------------------------------------------------------------------
    # 도구 생성
    # -------------------------------------------------------------------------

    def _create_tools(self, client: QTestClient) -> List[StructuredTool]:
        """QTestClient 를 캡처하는 클로저 방식으로 28개의 StructuredTool 을 생성하여 반환합니다."""
        tools = []

        # ====================================================================
        # 1. 테스트 케이스 생성 (기본)
        # ====================================================================

        def qtest_create_test_case(
            name: str,
            description: str = "",
            project_id: Optional[str] = None,
        ) -> str:
            if not name:
                return "ERROR: 테스트 케이스 이름이 필요합니다."
            pid = client.resolve_project_id(project_id)
            if not pid:
                return "ERROR: 프로젝트 ID가 필요합니다."
            data = client.request("POST", f"/api/v3/projects/{pid}/test-cases",
                                  payload={"name": name, "description": description or ""})
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            web_url = data.get("web_url") or (
                f"{client.qtest_url}/p/{pid}/portal/project#tab=testdesign&object=1&id={data.get('id')}"
            )
            return (
                f"SUCCESS: 테스트 케이스가 등록되었습니다.\n"
                f"ID: {data.get('id')} | PID: {data.get('pid')} | 이름: {data.get('name')}\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_case,
            name="qtest_create_test_case",
            description="테스트 케이스를 qTest에 등록합니다.",
            args_schema=CreateTestCaseSchema,
        ))

        # ====================================================================
        # 2. 테스트 스텝 포함 TC 생성
        # ====================================================================

        def qtest_create_test_case_with_steps(
            name: str,
            description: str = "",
            test_steps: Optional[List[dict]] = None,
            project_id: Optional[str] = None,
        ) -> str:
            if not name:
                return "ERROR: 테스트 케이스 이름이 필요합니다."
            pid = client.resolve_project_id(project_id)
            if not pid:
                return "ERROR: 프로젝트 ID가 필요합니다."

            steps = test_steps if test_steps else []
            if not steps and description:
                steps = _generate_test_steps(description)

            payload: dict = {"name": name, "description": description or ""}
            if steps:
                payload["test_steps"] = [
                    {
                        "description": s.get("description", s.get("step", "")),
                        "expected": s.get("expected_result", s.get("expectedResult", s.get("expected", ""))),
                        "order": i + 1,
                    }
                    for i, s in enumerate(steps)
                ]

            data = client.request("POST", f"/api/v3/projects/{pid}/test-cases", payload=payload)

            # Fallback: 스텝 없이 생성 후 개별 추가
            if isinstance(data, dict) and "error" in data:
                data = client.request("POST", f"/api/v3/projects/{pid}/test-cases",
                                      payload={"name": name, "description": description or ""})
                if isinstance(data, dict) and "error" in data:
                    return f"ERROR: {data['error']}"
                if steps:
                    tc_version_id = data.get("test_case_version_id") or data.get("testCaseVersionId")
                    if tc_version_id:
                        for idx, step in enumerate(steps):
                            client.request(
                                "POST",
                                f"/api/v3/projects/{pid}/test-cases/{data.get('id')}/versions/{tc_version_id}/test-steps",
                                payload={
                                    "description": step.get("description", step.get("step", "")),
                                    "expected": step.get("expected_result", step.get("expected", "")),
                                    "order": idx + 1,
                                },
                            )

            added_count = len(data.get("test_steps", [])) if isinstance(data, dict) else 0
            web_url = data.get("web_url") or (
                f"{client.qtest_url}/p/{pid}/portal/project#tab=testdesign&object=1&id={data.get('id')}"
            )
            return (
                f"SUCCESS: 테스트 케이스 생성 완료.\n"
                f"ID: {data.get('id')} | PID: {data.get('pid')} | 스텝: {added_count}개\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_case_with_steps,
            name="qtest_create_test_case_with_steps",
            description=(
                "테스트 케이스 이름과 내용을 받아서 테스트 스텝 포함 테스트 케이스를 생성하고 qTest에 등록합니다. "
                "test_steps 미제공 시 description에서 자동 생성합니다."
            ),
            args_schema=CreateTestCaseWithStepsSchema,
        ))

        # ====================================================================
        # 3. 프롬프트 기반 TC 자동 생성
        # ====================================================================

        def qtest_generate_and_create(
            name: str,
            content: str,
            project_id: Optional[str] = None,
        ) -> str:
            steps = _generate_test_steps(content)
            return qtest_create_test_case_with_steps(name, content, steps, project_id)

        tools.append(StructuredTool.from_function(
            func=qtest_generate_and_create,
            name="qtest_generate_and_create",
            description="테스트 케이스 이름과 내용을 받아 테스트 스텝을 자동 생성하고 qTest에 등록합니다.",
            args_schema=GenerateAndCreateSchema,
        ))

        # ====================================================================
        # 4. 프로젝트 목록 조회
        # ====================================================================

        def qtest_list_projects(page: int = 1, page_size: int = 25) -> str:
            data = client.request("GET", "/api/v3/projects", params={"page": page, "pageSize": page_size})
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            projects = data if isinstance(data, list) else []
            lines = [f"프로젝트 목록 (총 {len(projects)}개):"]
            for p in projects:
                lines.append(f"  ID: {p.get('id')} | 이름: {p.get('name')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_projects,
            name="qtest_list_projects",
            description="qTest의 모든 프로젝트 목록을 조회합니다.",
            args_schema=ListProjectsSchema,
        ))

        # ====================================================================
        # 5. 프로젝트 상세 조회
        # ====================================================================

        def qtest_get_project(project_id: str) -> str:
            data = client.request("GET", f"/api/v3/projects/{project_id}")
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return json.dumps(data, ensure_ascii=False, indent=2)

        tools.append(StructuredTool.from_function(
            func=qtest_get_project,
            name="qtest_get_project",
            description="프로젝트 ID로 qTest 프로젝트 상세 정보를 조회합니다.",
            args_schema=GetProjectSchema,
        ))

        # ====================================================================
        # 6. 요구사항 목록 조회
        # ====================================================================

        def qtest_list_requirements(
            project_id: Optional[str] = None,
            parent_id: Optional[int] = None,
            page: int = 1,
            size: int = 25,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            params = {"page": page, "size": size}
            if parent_id is not None:
                params["parentId"] = parent_id
            data = client.request("GET", f"/api/v3/projects/{pid}/requirements", params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            items = data if isinstance(data, list) else []
            lines = [f"요구사항 목록 (총 {len(items)}개):"]
            for r in items:
                lines.append(f"  ID: {r.get('id')} | PID: {r.get('pid')} | 이름: {r.get('name')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_requirements,
            name="qtest_list_requirements",
            description="qTest 프로젝트의 요구사항 목록을 조회합니다.",
            args_schema=ListRequirementsSchema,
        ))

        # ====================================================================
        # 7. 요구사항 상세 조회
        # ====================================================================

        def qtest_get_requirement(requirement_id: int, project_id: Optional[str] = None) -> str:
            pid = client.resolve_project_id(project_id)
            data = client.request("GET", f"/api/v3/projects/{pid}/requirements/{requirement_id}")
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return json.dumps(data, ensure_ascii=False, indent=2)

        tools.append(StructuredTool.from_function(
            func=qtest_get_requirement,
            name="qtest_get_requirement",
            description="특정 요구사항의 상세 정보를 조회합니다.",
            args_schema=GetRequirementSchema,
        ))

        # ====================================================================
        # 8. 요구사항에 연결된 TC 목록 조회
        # ====================================================================

        def qtest_list_testcases_for_requirements(
            requirement_ids: List[int],
            project_id: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            all_testcases: dict = {}
            for req_id in requirement_ids:
                data = client.request(
                    "GET", f"/api/v3/projects/{pid}/linked-artifacts",
                    params={"type": "test-cases", "ids": str(req_id), "sourceType": "requirements"},
                )
                items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
                for tc in items:
                    tc_id = tc.get("id")
                    if tc_id and tc_id not in all_testcases:
                        all_testcases[tc_id] = tc
            lines = [f"연결된 테스트 케이스 (총 {len(all_testcases)}개):"]
            for tc in all_testcases.values():
                lines.append(f"  ID: {tc.get('id')} | 이름: {tc.get('name')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_testcases_for_requirements,
            name="qtest_list_testcases_for_requirements",
            description="하나 이상의 요구사항에 연결된 테스트 케이스 목록을 조회합니다.",
            args_schema=ListTestcasesForRequirementsSchema,
        ))

        # ====================================================================
        # 9. TC를 요구사항에 연결
        # ====================================================================

        def qtest_link_testcases_to_requirement(
            requirement_id: int,
            testcase_ids: List[int],
            project_id: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            # type 쿼리 파라미터: 링크할 오브젝트 타입 = "test-cases"
            data = client.request(
                "POST", f"/api/v3/projects/{pid}/requirements/{requirement_id}/link?type=test-cases",
                payload=testcase_ids,
            )
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            linked = data if isinstance(data, list) else []
            return f"SUCCESS: {len(testcase_ids)}개의 테스트 케이스가 요구사항(ID: {requirement_id})에 연결되었습니다. 응답: {linked}"

        tools.append(StructuredTool.from_function(
            func=qtest_link_testcases_to_requirement,
            name="qtest_link_testcases_to_requirement",
            description="하나 이상의 테스트 케이스를 특정 요구사항에 연결(링크)합니다.",
            args_schema=LinkTestcasesToRequirementSchema,
        ))

        # ====================================================================
        # 10. 모듈 검색
        # ====================================================================

        def qtest_search_modules(
            project_id: Optional[str] = None,
            search: Optional[str] = None,
            parent_id: Optional[int] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            params = {}
            if search:
                params["search"] = search
            if parent_id is not None:
                params["parentId"] = parent_id
            data = client.request("GET", f"/api/v3/projects/{pid}/modules", params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            results = data if isinstance(data, list) else []
            if search:
                filtered = [m for m in results if search in m.get("name", "")]
                if filtered:
                    results = filtered
            lines = [f"모듈 목록 (총 {len(results)}개):"]
            for m in results:
                lines.append(f"  ID: {m.get('id')} | 이름: {m.get('name')} | PID: {m.get('pid')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_search_modules,
            name="qtest_search_modules",
            description="qTest 프로젝트에서 모듈을 이름으로 검색합니다.",
            args_schema=SearchModulesSchema,
        ))

        # ====================================================================
        # 11. 모듈 생성
        # ====================================================================

        def qtest_create_module(
            name: str,
            project_id: Optional[str] = None,
            description: str = "",
            parent_id: Optional[int] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            payload: dict = {"name": name}
            if description:
                payload["description"] = description
            params = {}
            if parent_id is not None:
                params["parentId"] = parent_id
            data = client.request("POST", f"/api/v3/projects/{pid}/modules", payload=payload, params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return f"SUCCESS: 모듈 '{name}'이(가) 생성되었습니다. (ID: {data.get('id')})"

        tools.append(StructuredTool.from_function(
            func=qtest_create_module,
            name="qtest_create_module",
            description="qTest 프로젝트에 새 모듈을 생성합니다.",
            args_schema=CreateModuleSchema,
        ))

        # ====================================================================
        # 12. TC 목록 조회
        # ====================================================================

        def qtest_list_testcases(
            project_id: Optional[str] = None,
            parent_id: Optional[int] = None,
            page: int = 1,
            size: int = 25,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            params = {"page": page, "size": size}
            if parent_id is not None:
                params["parentId"] = parent_id
            data = client.request("GET", f"/api/v3/projects/{pid}/test-cases", params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            items = (
                data if isinstance(data, list)
                else data.get("items", []) if isinstance(data, dict) and "items" in data
                else []
            )
            total = len(data) if isinstance(data, list) else data.get("total", len(items)) if isinstance(data, dict) else len(items)
            lines = [f"테스트 케이스 목록 (총 {total}개, 현재 {len(items)}개):"]
            for tc in items:
                lines.append(f"  ID: {tc.get('id')} | PID: {tc.get('pid')} | 이름: {tc.get('name')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_testcases,
            name="qtest_list_testcases",
            description="qTest 프로젝트의 테스트 케이스 목록을 조회합니다.",
            args_schema=ListTestcasesSchema,
        ))

        # ====================================================================
        # 13. TC 상세 조회
        # ====================================================================

        def qtest_get_testcase(
            testcase_id: int,
            project_id: Optional[str] = None,
            expand: Optional[str] = None,
            version_id: Optional[int] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            params = {}
            if expand:
                params["expand"] = expand
            if version_id is not None:
                params["versionId"] = version_id
            data = client.request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}", params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return json.dumps(data, ensure_ascii=False, indent=2)

        tools.append(StructuredTool.from_function(
            func=qtest_get_testcase,
            name="qtest_get_testcase",
            description="특정 테스트 케이스의 상세 정보를 조회합니다. expand='teststep'으로 테스트 스텝도 함께 조회할 수 있습니다.",
            args_schema=GetTestcaseSchema,
        ))

        # ====================================================================
        # 14. TC 수정
        # ====================================================================

        def qtest_update_testcase(
            testcase_id: int,
            project_id: Optional[str] = None,
            name: Optional[str] = None,
            description: Optional[str] = None,
            precondition: Optional[str] = None,
            parent_id: Optional[int] = None,
            test_steps: Optional[List[dict]] = None,
            automation_content: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            existing = client.request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}")
            if isinstance(existing, dict) and "error" in existing:
                return f"ERROR: {existing['error']}"

            payload: dict = {"name": name if name is not None else existing.get("name", "")}
            if description is not None:
                payload["description"] = description
            if precondition is not None:
                payload["precondition"] = precondition
            if parent_id is not None:
                payload["parent_id"] = parent_id
            if test_steps is not None:
                payload["test_steps"] = [
                    {"description": s.get("description", ""), "expected": s.get("expected_result", s.get("expected", "")), "order": i + 1}
                    for i, s in enumerate(test_steps)
                ]
            if automation_content is not None:
                existing_props = existing.get("properties", [])
                properties = []
                for prop in existing_props:
                    if prop.get("field_name") == "Automation Content":
                        properties.append({"field_id": prop["field_id"], "field_value": automation_content})
                    elif prop.get("field_name") == "Automation":
                        properties.append({"field_id": prop["field_id"], "field_value": "711"})  # Yes
                if properties:
                    payload["properties"] = properties

            data = client.request("PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}", payload=payload)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return f"SUCCESS: 테스트 케이스(ID: {testcase_id})가 수정되었습니다."

        tools.append(StructuredTool.from_function(
            func=qtest_update_testcase,
            name="qtest_update_testcase",
            description="기존 테스트 케이스를 수정합니다. (이름, 설명, 사전조건, 테스트 스텝, Automation Content 등)",
            args_schema=UpdateTestcaseSchema,
        ))

        # ====================================================================
        # 15. TC Property 업데이트
        # ====================================================================

        def qtest_update_testcase_property(
            testcase_id: int,
            field_name: str,
            field_value: str,
            project_id: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            existing = client.request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}")
            if isinstance(existing, dict) and "error" in existing:
                return f"ERROR: {existing['error']}"

            existing_props = existing.get("properties", [])
            target_field_id = None
            for prop in existing_props:
                if prop.get("field_name", "").lower() == field_name.lower():
                    target_field_id = prop.get("field_id")
                    break

            if target_field_id is None:
                available = [p.get("field_name") for p in existing_props if p.get("field_name")]
                return f"ERROR: '{field_name}' Property를 찾을 수 없습니다. 사용 가능: {', '.join(available)}"

            # 기존 properties 전체를 유지하면서 해당 field만 교체
            updated_props = []
            for prop in existing_props:
                if prop.get("field_id") == target_field_id:
                    updated_props.append({"field_id": target_field_id, "field_value": field_value})
                else:
                    updated_props.append({"field_id": prop.get("field_id"), "field_value": prop.get("field_value", "")})

            data = client.request(
                "PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}",
                payload={"name": existing.get("name", ""), "properties": updated_props},
            )
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return f"SUCCESS: 테스트 케이스(ID: {testcase_id})의 '{field_name}'이(가) '{field_value}'(으)로 변경되었습니다."

        tools.append(StructuredTool.from_function(
            func=qtest_update_testcase_property,
            name="qtest_update_testcase_property",
            description="테스트 케이스의 특정 Property(속성)를 업데이트합니다. Automation Content, Priority, Status 등을 field_name으로 지정하여 변경합니다.",
            args_schema=UpdateTestcasePropertySchema,
        ))

        # ====================================================================
        # 16. TC에 연결된 Test Run 목록 조회
        # ====================================================================

        def qtest_list_testruns_for_testcases(
            testcase_ids: List[int],
            project_id: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            all_runs = []
            for tc_id in testcase_ids:
                data = client.request("GET", f"/api/v3/projects/{pid}/test-runs",
                                      params={"parentId": tc_id, "parentType": "test-case"})
                if isinstance(data, list):
                    all_runs.extend(data)
                elif isinstance(data, dict) and "items" in data:
                    all_runs.extend(data["items"])
                else:
                    alt = client.request("GET", f"/api/v3/projects/{pid}/test-cases/{tc_id}/test-runs")
                    if isinstance(alt, list):
                        all_runs.extend(alt)
            lines = [f"Test Run 목록 (총 {len(all_runs)}개):"]
            for r in all_runs:
                lines.append(
                    f"  ID: {r.get('id')} | 이름: {r.get('name')} | "
                    f"상태: {r.get('latest_status', {}).get('name', 'N/A')}"
                )
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_testruns_for_testcases,
            name="qtest_list_testruns_for_testcases",
            description="하나 이상의 테스트 케이스에 연결된 테스트 런 목록을 조회합니다.",
            args_schema=ListTestrunsForTestcasesSchema,
        ))

        # ====================================================================
        # 17. Test Run의 Test Log 목록 조회
        # ====================================================================

        def qtest_list_testlogs_for_testrun(
            test_run_id: int,
            project_id: Optional[str] = None,
            page: int = 1,
            page_size: int = 25,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            data = client.request(
                "GET", f"/api/v3/projects/{pid}/test-runs/{test_run_id}/test-logs",
                params={"page": page, "pageSize": page_size},
            )
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
            lines = [f"Test Log 목록 (총 {len(items)}개):"]
            for log in items:
                lines.append(
                    f"  ID: {log.get('id')} | "
                    f"상태: {log.get('status', {}).get('name', 'N/A')} | "
                    f"날짜: {log.get('exe_start_date', '')}"
                )
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_list_testlogs_for_testrun,
            name="qtest_list_testlogs_for_testrun",
            description="특정 테스트 런의 테스트 로그(실행 기록) 목록을 조회합니다.",
            args_schema=ListTestlogsForTestrunSchema,
        ))

        # ====================================================================
        # 18. 결함 생성
        # ====================================================================

        def qtest_create_defect(
            test_log_id: int,
            summary: str,
            description: str,
            project_id: Optional[str] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            data = client.request("POST", f"/api/v3/projects/{pid}/defects",
                                  payload={"name": summary, "description": description, "test_logs": [test_log_id]})
            if isinstance(data, dict) and "error" in data:
                data = client.request("POST", f"/api/v3/projects/{pid}/defects",
                                      payload={"name": summary, "description": description})
                if isinstance(data, dict) and "error" in data:
                    return f"ERROR: {data['error']}"
            return f"SUCCESS: 결함이 생성되었습니다. (ID: {data.get('id')})"

        tools.append(StructuredTool.from_function(
            func=qtest_create_defect,
            name="qtest_create_defect",
            description="테스트 로그에 대한 결함(Defect)을 생성합니다.",
            args_schema=CreateDefectSchema,
        ))

        # ====================================================================
        # 19. 통합 검색
        # ====================================================================

        def qtest_search_objects(
            object_type: str,
            query: str,
            project_id: Optional[str] = None,
            search_field: str = "name",
            exact_match: bool = False,
            page: int = 1,
            size: int = 25,
        ) -> str:
            valid_types = ["releases", "requirements", "test-cases", "test-runs", "test-suites",
                           "test-cycles", "test-logs", "builds", "defects"]
            if object_type not in valid_types:
                return f"ERROR: 지원되지 않는 객체 유형. 지원 유형: {', '.join(valid_types)}"

            pid = client.resolve_project_id(project_id)
            query_str = f"'{search_field}' = '{query}'" if exact_match else f"'{search_field}' ~ '{query}'"
            data = client.request(
                "POST", f"/api/v3/projects/{pid}/search",
                payload={"object_type": object_type, "fields": ["*"], "query": query_str},
                params={"page": page, "pageSize": size},
            )
            if isinstance(data, dict) and "error" in data:
                # Fallback: list endpoint + filter
                data = client.request("GET", f"/api/v3/projects/{pid}/{object_type}",
                                      params={"page": page, "size": size})
                if isinstance(data, dict) and "error" in data:
                    return f"ERROR: {data['error']}"
                items = data if isinstance(data, list) else []
                if search_field == "name":
                    items = [o for o in items if (o.get("name") == query if exact_match else query.lower() in o.get("name", "").lower())]
                elif search_field == "id":
                    items = [o for o in items if str(o.get("id")) == str(query)]
            else:
                items = data.get("items", data.get("results", [])) if isinstance(data, dict) else data if isinstance(data, list) else []

            total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
            lines = [f"검색 결과 ({object_type}, 총 {total}개):"]
            for item in items:
                lines.append(f"  ID: {item.get('id')} | 이름: {item.get('name')}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_search_objects,
            name="qtest_search_objects",
            description="qTest 프로젝트 내에서 다양한 객체(요구사항, 테스트케이스, 테스트런, 결함 등)를 검색합니다.",
            args_schema=SearchObjectsSchema,
        ))

        # ====================================================================
        # 20. TC 승인
        # ====================================================================

        def qtest_approve_test_case(testcase_id: int, project_id: Optional[str] = None) -> str:
            pid = client.resolve_project_id(project_id)
            data = client.request("PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}/approve")
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            return f"SUCCESS: 테스트 케이스(ID: {testcase_id})가 승인(Approve)되었습니다."

        tools.append(StructuredTool.from_function(
            func=qtest_approve_test_case,
            name="qtest_approve_test_case",
            description="테스트 케이스를 승인(Approve)합니다. 승인된 테스트 케이스만 Test Execution에서 실행할 수 있습니다.",
            args_schema=ApproveTestCaseSchema,
        ))

        # ====================================================================
        # 21. TC 일괄 승인
        # ====================================================================

        def qtest_approve_test_cases_bulk(testcase_ids: List[int], project_id: Optional[str] = None) -> str:
            pid = client.resolve_project_id(project_id)
            approved, failed = [], []
            for tc_id in testcase_ids:
                data = client.request("PUT", f"/api/v3/projects/{pid}/test-cases/{tc_id}/approve")
                if isinstance(data, dict) and "error" in data:
                    failed.append(f"ID {tc_id}: {data['error']}")
                else:
                    approved.append(tc_id)
            lines = [f"일괄 승인 결과: {len(approved)}개 성공, {len(failed)}개 실패"]
            if approved:
                lines.append(f"  승인 완료: {approved}")
            if failed:
                lines.append(f"  실패: {failed}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_approve_test_cases_bulk,
            name="qtest_approve_test_cases_bulk",
            description="여러 테스트 케이스를 한번에 승인(Approve)합니다.",
            args_schema=ApproveTestCasesBulkSchema,
        ))

        # ====================================================================
        # 22. Test Cycle 생성
        # ====================================================================

        def qtest_create_test_cycle(
            name: str,
            project_id: Optional[str] = None,
            description: str = "",
            parent_id: int = 0,
            parent_type: str = "root",
        ) -> str:
            pid = client.resolve_project_id(project_id)
            payload: dict = {"name": name}
            if description:
                payload["description"] = description
            params = {}
            if parent_id and parent_id != 0:
                params["parentId"] = parent_id
            if parent_type:
                params["parentType"] = parent_type
            data = client.request("POST", f"/api/v3/projects/{pid}/test-cycles", payload=payload, params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            web_url = data.get("web_url") or (
                f"{client.qtest_url}/p/{pid}/portal/project#tab=testexecution&object=2&id={data.get('id')}"
            )
            return (
                f"SUCCESS: Test Cycle '{name}'이(가) 생성되었습니다.\n"
                f"ID: {data.get('id')} | PID: {data.get('pid')}\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_cycle,
            name="qtest_create_test_cycle",
            description="Test Execution에 새로운 Test Cycle을 생성합니다. Test Suite와 Test Run을 담는 상위 컨테이너입니다.",
            args_schema=CreateTestCycleSchema,
        ))

        # ====================================================================
        # 23. Test Suite 생성
        # ====================================================================

        def qtest_create_test_suite(
            name: str,
            project_id: Optional[str] = None,
            description: str = "",
            parent_id: int = 0,
            parent_type: str = "root",
        ) -> str:
            pid = client.resolve_project_id(project_id)
            payload: dict = {"name": name}
            if description:
                payload["description"] = description
            params = {}
            if parent_id and parent_id != 0:
                params["parentId"] = parent_id
            if parent_type:
                params["parentType"] = parent_type
            data = client.request("POST", f"/api/v3/projects/{pid}/test-suites", payload=payload, params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            web_url = data.get("web_url") or (
                f"{client.qtest_url}/p/{pid}/portal/project#tab=testexecution&object=5&id={data.get('id')}"
            )
            return (
                f"SUCCESS: Test Suite '{name}'이(가) 생성되었습니다.\n"
                f"ID: {data.get('id')} | PID: {data.get('pid')}\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_suite,
            name="qtest_create_test_suite",
            description="Test Execution에 새로운 Test Suite를 생성합니다. Test Run을 그룹화하는 컨테이너입니다.",
            args_schema=CreateTestSuiteSchema,
        ))

        # ====================================================================
        # 24. Test Run 생성
        # ====================================================================

        def qtest_create_test_run(
            name: str,
            test_case_id: int,
            project_id: Optional[str] = None,
            description: str = "",
            parent_id: int = 0,
            parent_type: str = "root",
            test_case_version_id: Optional[int] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            tc_info: dict = {"id": test_case_id}
            if test_case_version_id is not None:
                tc_info["test_case_version_id"] = test_case_version_id
            payload: dict = {"name": name, "test_case": tc_info}
            if description:
                payload["description"] = description
            params = {}
            if parent_id and parent_id != 0:
                params["parentId"] = parent_id
            if parent_type:
                params["parentType"] = parent_type
            data = client.request("POST", f"/api/v3/projects/{pid}/test-runs", payload=payload, params=params)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            web_url = data.get("web_url") or (
                f"{client.qtest_url}/p/{pid}/portal/project#tab=testexecution&object=3&id={data.get('id')}"
            )
            return (
                f"SUCCESS: Test Run '{name}'이(가) 생성되었습니다.\n"
                f"ID: {data.get('id')} | PID: {data.get('pid')} | TC ID: {test_case_id}\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_run,
            name="qtest_create_test_run",
            description="Test Execution에 새로운 Test Run을 생성합니다. 승인된 TC여야 하며 Test Suite/Cycle 하위에 생성 가능합니다.",
            args_schema=CreateTestRunSchema,
        ))

        # ====================================================================
        # 25. Test Run 일괄 생성
        # ====================================================================

        def qtest_create_test_runs_bulk(
            test_case_ids: List[int],
            project_id: Optional[str] = None,
            parent_id: int = 0,
            parent_type: str = "test-suite",
        ) -> str:
            pid = client.resolve_project_id(project_id)
            created, failed = [], []
            for tc_id in test_case_ids:
                tc_data = client.request("GET", f"/api/v3/projects/{pid}/test-cases/{tc_id}")
                tc_name = (
                    tc_data.get("name", f"TC-{tc_id}")
                    if isinstance(tc_data, dict) and "error" not in tc_data
                    else f"TC-{tc_id}"
                )
                params = {}
                if parent_id and parent_id != 0:
                    params["parentId"] = parent_id
                if parent_type:
                    params["parentType"] = parent_type
                data = client.request("POST", f"/api/v3/projects/{pid}/test-runs",
                                      payload={"name": tc_name, "test_case": {"id": tc_id}}, params=params)
                if isinstance(data, dict) and "error" in data:
                    failed.append({"tc_id": tc_id, "error": data["error"]})
                else:
                    created.append({"tc_id": tc_id, "run_id": data.get("id"), "name": data.get("name")})
            lines = [f"Test Run 일괄 생성: {len(created)}개 성공, {len(failed)}개 실패"]
            for c in created:
                lines.append(f"  TC {c['tc_id']} → Run ID: {c['run_id']} ({c['name']})")
            if failed:
                lines.append("  실패:")
                for f_item in failed:
                    lines.append(f"    TC {f_item['tc_id']}: {f_item['error']}")
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_create_test_runs_bulk,
            name="qtest_create_test_runs_bulk",
            description="여러 테스트 케이스에 대한 Test Run을 한번에 생성합니다. 지정한 Test Suite 또는 Test Cycle 하위에 일괄 생성됩니다.",
            args_schema=CreateTestRunsBulkSchema,
        ))

        # ====================================================================
        # 26. Automation Agent 조회 (Host/Agent 통합)
        # ====================================================================

        def qtest_search_automation_agents(
            project_id: Optional[str] = None,
            host_id: Optional[int] = None,
            agent_name: Optional[str] = None,
            framework: Optional[str] = None,
            active_only: bool = True,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            search_payload: dict = {"project_ids": [int(pid)]}
            if host_id is not None:
                search_payload["host_id"] = host_id
            data = client.request("POST", "/api/v3/automation/automation-agents", payload=search_payload)
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            agents_list = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []

            if active_only:
                agents_list = [a for a in agents_list if a.get("active", False)]
            if agent_name:
                agents_list = [a for a in agents_list if agent_name.lower() in a.get("name", "").lower()]
            if framework:
                agents_list = [
                    a for a in agents_list
                    if framework.lower() in a.get("framework", "").lower()
                    or framework.lower() in a.get("framework_id", "").lower()
                ]

            lines = [f"Automation Agent 목록 (총 {len(agents_list)}개):"]
            for a in agents_list:
                lines.append(
                    f"  ID: {a.get('agent_server_id', a.get('id'))} | "
                    f"이름: {a.get('name', '')} | 프레임워크: {a.get('framework', '')} | "
                    f"Host: {a.get('host_name', '')} (ID: {a.get('host_id', '')}) | 활성: {a.get('active', False)}"
                )
            return "\n".join(lines)

        tools.append(StructuredTool.from_function(
            func=qtest_search_automation_agents,
            name="qtest_search_automation_agents",
            description="qTest에 등록된 Automation Agent 목록을 조회합니다. host_id로 특정 Host의 Agent만 필터링하거나, agent_name/framework으로 검색할 수 있습니다.",
            args_schema=SearchAutomationAgentsSchema,
        ))

        # ====================================================================
        # 27. Automation Schedule 생성 (즉시 실행)
        # ====================================================================

        def qtest_create_automation_schedule(
            test_run_ids: List[int],
            agent_id: int,
            project_id: Optional[str] = None,
            host_id: Optional[int] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            headers = client._get_headers()
            payload_camel = {
                "clientId": agent_id,
                "name": "MCP Schedule",
                "agentId": agent_id,
                "startDate": now_iso,
                "creator": 0,
                "projectId": int(pid),
                "testRunIds": test_run_ids,
                "dynamic": {},
            }
            if host_id is not None:
                payload_camel["hostId"] = host_id

            attempts = [
                f"{client.qtest_url}/api/v3/automation/jobs/schedule/create",
            ]
            last_error = ""
            with httpx.Client(timeout=30.0) as http_client:
                for url in attempts:
                    try:
                        body = json.dumps(payload_camel, ensure_ascii=False).encode("utf-8")
                        response = http_client.post(url, headers=headers, content=body)
                        response.raise_for_status()
                        resp_data = {} if response.status_code == 204 or not response.content else response.json()
                        job_id = (
                            resp_data if isinstance(resp_data, int)
                            else resp_data.get("id", "") if isinstance(resp_data, dict)
                            else resp_data
                        )
                        return (
                            f"SUCCESS: Automation Schedule이 생성되었습니다.\n"
                            f"Job ID: {job_id} | Agent ID: {agent_id}\n"
                            f"Test Run 수: {len(test_run_ids)}개"
                        )
                    except httpx.HTTPStatusError as e:
                        try:
                            err_body = e.response.json()
                            last_error = err_body.get("message", str(e)) if isinstance(err_body, dict) else str(e)
                        except Exception:
                            last_error = str(e)
                    except Exception as e:
                        last_error = str(e)
            return f"ERROR: 모든 엔드포인트 시도 실패. 마지막 에러: {last_error}"

        tools.append(StructuredTool.from_function(
            func=qtest_create_automation_schedule,
            name="qtest_create_automation_schedule",
            description="Automation Schedule을 생성하여 테스트를 즉시 실행합니다. Test Run ID 배열과 Agent ID를 지정합니다.",
            args_schema=CreateAutomationScheduleSchema,
        ))


        # ====================================================================
        # 28. Auto Test Log 제출 (실행 결과 기록)
        # ====================================================================

        def qtest_submit_auto_test_log(
            test_run_id: int,
            status: str,
            project_id: Optional[str] = None,
            name: Optional[str] = None,
            note: str = "",
            automation_content: str = "",
            test_step_logs: Optional[List[dict]] = None,
        ) -> str:
            pid = client.resolve_project_id(project_id)
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            if not name:
                tr_data = client.request("GET", f"/api/v3/projects/{pid}/test-runs/{test_run_id}")
                name = (
                    tr_data.get("name", f"TR-{test_run_id}")
                    if isinstance(tr_data, dict) and "error" not in tr_data
                    else f"TR-{test_run_id}"
                )
            STATUS_MAP = {
                "PASSED": {"id": 601, "name": "PASSED"},
                "FAILED": {"id": 602, "name": "FAILED"},
                "INCOMPLETE": {"id": 603, "name": "INCOMPLETE"},
                "BLOCKED": {"id": 604, "name": "BLOCKED"},
                "UNEXECUTED": {"id": 605, "name": "UNEXECUTED"},
                "NOT_RUN": {"id": 605, "name": "UNEXECUTED"},
            }
            status_obj = STATUS_MAP.get(status.upper(), {"id": 602, "name": "FAILED"})
            payload: dict = {
                "exe_start_date": now_iso,
                "exe_end_date": now_iso,
                "name": name,
                "status": status_obj,
                "note": note or "",
            }
            if automation_content:
                payload["automation_content"] = automation_content
            if test_step_logs:
                payload["test_step_logs"] = [
                    {
                        "description": s.get("description", ""),
                        "expected_result": s.get("expected_result", s.get("expected", "")),
                        "actual_result": s.get("actual_result", s.get("actual", "")),
                        "status": STATUS_MAP.get(s.get("status", status).upper(), status_obj),
                        "order": i,
                    }
                    for i, s in enumerate(test_step_logs)
                ]
            data = client.request(
                "POST", f"/api/v3/projects/{pid}/test-runs/{test_run_id}/auto-test-logs",
                payload=payload,
            )
            if isinstance(data, dict) and "error" in data:
                return f"ERROR: {data['error']}"
            log_id = data.get("id", "") if isinstance(data, dict) else data
            web_url = f"{client.qtest_url}/p/{pid}/portal/project#tab=testexecution&object=3&id={test_run_id}"
            return (
                f"SUCCESS: 테스트 실행 결과가 기록되었습니다.\n"
                f"Test Run ID: {test_run_id} | 결과: {status.upper()} | Log ID: {log_id}\n"
                f"URL: {web_url}"
            )

        tools.append(StructuredTool.from_function(
            func=qtest_submit_auto_test_log,
            name="qtest_submit_auto_test_log",
            description="Test Run에 자동화 테스트 실행 결과(Auto Test Log)를 제출합니다. PASSED/FAILED 등으로 기록합니다.",
            args_schema=SubmitAutoTestLogSchema,
        ))

        return tools

    # -------------------------------------------------------------------------
    # execute
    # -------------------------------------------------------------------------

    def execute(self, *args, **kwargs):
        """qTest MCP 노드 실행 - qTest 연동 도구 목록(dict)을 반환합니다."""
        try:
            qtest_url = (kwargs.get("qtest_url") or "").rstrip("/")
            qtest_token = kwargs.get("qtest_token") or ""
            qtest_username = kwargs.get("qtest_username") or ""
            qtest_password = kwargs.get("qtest_password") or ""
            default_project_id = kwargs.get("default_project_id") or ""

            if not qtest_url:
                raise ValueError("qTest URL이 필요합니다. 파라미터에 qtest_url을 설정해주세요.")

            logger.info("=" * 80)
            logger.info("🚀 qTest MCP Node Execution Started")
            logger.info(f"  ├─ qTest URL: {qtest_url}")
            logger.info(f"  ├─ Auth: {'Token' if qtest_token else 'Username/Password' if qtest_username else 'NONE'}")
            logger.info(f"  └─ Default Project ID: {default_project_id or '(not set)'}")

            token = self._get_auth_token(qtest_url, qtest_token, qtest_username, qtest_password)
            qtest_client = QTestClient(qtest_url, token, default_project_id)

            tools = self._create_tools(qtest_client)

            logger.info(f"✅ Created {len(tools)} qTest tools:")
            for tool in tools:
                logger.info(f"     • {tool.name}")
            logger.info("=" * 80)

            return {"tools": tools}

        except Exception as e:
            logger.error("=" * 80)
            logger.error("❌ qTest MCP Node execution error:")
            logger.error(f"   └─ {str(e)}")
            logger.error("=" * 80)
            raise e
