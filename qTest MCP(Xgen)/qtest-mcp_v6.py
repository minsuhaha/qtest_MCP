## Test Execution 기능 추가

import os, sys, asyncio, argparse, io, json, re, base64, time
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from typing import Annotated, Optional, List, Dict
from pydantic import Field
import httpx


# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 환경변수 로드
load_dotenv()

# qTest 설정
QTEST_URL = (os.environ.get("QTEST_URL", "https://plateer.qtestnet.com")).rstrip('/')
QTEST_TOKEN = os.environ.get("QTEST_TOKEN", "")     # 환경변수 또는 HTTP 헤더로 주입
QTEST_USERNAME = os.environ.get("QTEST_USERNAME", "")     # 또는 username/password로 자동 로그인
QTEST_PASSWORD = os.environ.get("QTEST_PASSWORD", "")
DEFAULT_PROJECT_ID = os.environ.get("QTEST_PROJECT_ID", "127369")


# FastMCP 서버 초기화
mcp = FastMCP(name="qtest-mcp")


# ============================================================================
# 인증 관리
# ============================================================================

class QTestAuth:
    """qTest 인증 관리.

    우선순위:
      1. QTEST_TOKEN 환경변수가 있으면 → 해당 토큰을 그대로 사용
      2. QTEST_USERNAME + QTEST_PASSWORD가 있으면 → POST /oauth/token 으로 로그인
    """

    def __init__(self):
        self._access_token: Optional[str] = QTEST_TOKEN or None
        self._expires_at: float = float('inf') if QTEST_TOKEN else 0

    async def get_token(self) -> str:
        """유효한 토큰을 반환. 만료되었거나 없으면 자동 로그인."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        # 고정 토큰이 있으면 로그인 불필요
        if QTEST_TOKEN:
            self._access_token = QTEST_TOKEN
            self._expires_at = float('inf')
            return self._access_token
        # username/password로 로그인
        await self._login()
        return self._access_token

    async def _login(self):
        """POST /oauth/token 으로 로그인하여 세션 토큰 발급."""
        if not QTEST_USERNAME or not QTEST_PASSWORD:
            raise RuntimeError(
                "인증 정보가 없습니다. .env 파일에 다음 중 하나를 설정해주세요:\n"
                "  방법1) QTEST_TOKEN=로그인세션토큰값\n"
                "  방법2) QTEST_USERNAME=email@example.com + QTEST_PASSWORD=비밀번호"
            )

        # Authorization: Basic base64("{sitename}:")
        site_name = QTEST_URL.replace("https://", "").replace("http://", "").split(".")[0]
        basic_auth = base64.b64encode(f"{site_name}:".encode()).decode()

        headers = {
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = f"grant_type=password&username={QTEST_USERNAME}&password={QTEST_PASSWORD}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{QTEST_URL}/oauth/token", headers=headers, content=body)
            response.raise_for_status()
            data = response.json()

        self._access_token = f"bearer {data['access_token']}"
        expires_in = data.get("expires_in", 3600)
        self._expires_at = time.time() + expires_in
        print(f"[qTest Auth] 로그인 성공 (만료: {expires_in}초 후)", file=sys.stderr)


# 전역 인증 인스턴스
_auth = QTestAuth()


# ============================================================================
# 공통 유틸리티 함수
# ============================================================================

async def _get_headers() -> dict:
    # mcp-remote를 통해 전달된 HTTP Authorization 헤더 우선 사용
    http_headers = get_http_headers()
    auth = http_headers.get("authorization") or http_headers.get("Authorization")
    if auth:
        return {
            "Authorization": auth,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
    # 폴백: 환경변수 또는 username/password 로그인
    token = await _auth.get_token()
    return {
        "Authorization": token,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }


def _resolve_project_id(project_id: str = None) -> str:
    return project_id or DEFAULT_PROJECT_ID


async def _api_request(method: str, path: str, payload=None, params: dict = None, timeout: float = 30.0) -> dict:
    try:
        headers = await _get_headers()
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"❌ 인증 실패: {str(e)}"}

    url = f"{QTEST_URL}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method.upper() == "POST":
                if payload is not None:
                    json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                    response = await client.post(url, headers=headers, content=json_data, params=params)
                else:
                    response = await client.post(url, headers=headers, params=params)
            elif method.upper() == "PUT":
                if payload is not None:
                    json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                    response = await client.put(url, headers=headers, content=json_data, params=params)
                else:
                    response = await client.put(url, headers=headers, params=params)
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
        return {"error": f"❌ API 요청 실패 ({e.response.status_code}): {error_msg}"}
    except Exception as e:
        return {"error": f"❌ 오류 발생: {str(e)}"}


# ============================================================================
# 기존: 테스트 케이스 생성 (내부 함수)
# ============================================================================

async def create_test_case(name: str, description: str = "", project_id: str = None) -> dict:
    if not name:
        return {"error": "테스트 케이스 이름이 필요합니다."}
    project_id = _resolve_project_id(project_id)
    if not project_id:
        return {"error": "프로젝트 ID가 필요합니다."}

    payload = {"name": name, "description": description or ""}
    data = await _api_request("POST", f"/api/v3/projects/{project_id}/test-cases", payload=payload)
    if "error" in data:
        return data

    web_url = data.get("web_url") or f"{QTEST_URL}/p/{project_id}/portal/project#tab=testdesign&object=1&id={data.get('id')}"
    return {
        "success": True,
        "message": f"✅ 테스트 케이스가 성공적으로 등록되었습니다!\n\nID: {data.get('id')}\n이름: {data.get('name')}\nPID: {data.get('pid')}\nURL: {web_url}",
        "id": data.get("id"), "pid": data.get("pid"), "name": data.get("name"), "web_url": web_url
    }


async def add_test_steps(test_case_id: str, test_case_version_id: str, test_steps: List[Dict], project_id: str) -> int:
    headers = await _get_headers()
    added_count = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for idx, step in enumerate(test_steps):
            try:
                step_payload = {
                    "description": step.get("description", step.get("step", "")),
                    "expected": step.get("expected_result", step.get("expectedResult", step.get("expected", ""))),
                    "order": idx + 1
                }
                json_data = json.dumps(step_payload, ensure_ascii=False).encode('utf-8')
                response = await client.post(
                    f"{QTEST_URL}/api/v3/projects/{project_id}/test-cases/{test_case_id}/versions/{test_case_version_id}/test-steps",
                    headers=headers, content=json_data
                )
                response.raise_for_status()
                added_count += 1
            except Exception as e:
                print(f"테스트 스텝 {idx + 1} 추가 실패: {e}", file=sys.stderr)
    return added_count


async def create_test_case_with_steps(name: str, description: str = "", test_steps: List[Dict] = None, project_id: str = None) -> dict:
    if not name:
        return {"error": "테스트 케이스 이름이 필요합니다."}
    project_id = _resolve_project_id(project_id)
    if not project_id:
        return {"error": "프로젝트 ID가 필요합니다."}

    try:
        headers = await _get_headers()
    except Exception as e:
        return {"error": f"❌ 인증 실패: {str(e)}"}
    test_steps_list = test_steps if test_steps is not None else []
    added_steps_count = 0

    try:
        payload = {"name": name, "description": description or ""}
        if test_steps_list:
            payload["test_steps"] = []
            for idx, step in enumerate(test_steps_list):
                step_dict = step if isinstance(step, dict) else {}
                payload["test_steps"].append({
                    "description": step_dict.get("description", step_dict.get("step", "")),
                    "expected": step_dict.get("expected_result", step_dict.get("expectedResult", step_dict.get("expected", ""))),
                    "order": idx + 1
                })

        json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{QTEST_URL}/api/v3/projects/{project_id}/test-cases", headers=headers, content=json_data)
            response.raise_for_status()
            data = response.json()
            added_steps_count = len(data.get("test_steps", []))

            if test_steps_list and added_steps_count == 0:
                test_case_version_id = data.get("test_case_version_id") or data.get("testCaseVersionId")
                if test_case_version_id:
                    added_steps_count = await add_test_steps(data.get("id"), test_case_version_id, test_steps_list, project_id)

            web_url = data.get("web_url") or f"{QTEST_URL}/p/{project_id}/portal/project#tab=testdesign&object=1&id={data.get('id')}"
            return {
                "success": True,
                "message": f"✅ 테스트 케이스 생성 완료!\n\nID: {data.get('id')} | PID: {data.get('pid')} | 스텝: {added_steps_count}개\nURL: {web_url}",
                "id": data.get("id"), "pid": data.get("pid"), "name": data.get("name"),
                "test_steps_count": added_steps_count, "web_url": web_url
            }
    except httpx.HTTPStatusError as e:
        if e.response.status_code in [400, 422]:
            try:
                basic_payload = {"name": name, "description": description or ""}
                json_data = json.dumps(basic_payload, ensure_ascii=False).encode('utf-8')
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(f"{QTEST_URL}/api/v3/projects/{project_id}/test-cases", headers=headers, content=json_data)
                    response.raise_for_status()
                    data = response.json()
                    if test_steps_list:
                        test_case_version_id = data.get("test_case_version_id") or data.get("testCaseVersionId")
                        if test_case_version_id:
                            added_steps_count = await add_test_steps(data.get("id"), test_case_version_id, test_steps_list, project_id)
                    web_url = data.get("web_url") or f"{QTEST_URL}/p/{project_id}/portal/project#tab=testdesign&object=1&id={data.get('id')}"
                    return {
                        "success": True,
                        "message": f"✅ 테스트 케이스 생성 완료!\n\nID: {data.get('id')} | PID: {data.get('pid')} | 스텝: {added_steps_count}개\nURL: {web_url}",
                        "id": data.get("id"), "pid": data.get("pid"), "name": data.get("name"),
                        "test_steps_count": added_steps_count, "web_url": web_url
                    }
            except Exception as inner_e:
                error_msg = str(inner_e)
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", {}).get("message", error_msg)
                except Exception:
                    pass
                return {"error": f"❌ 테스트 케이스 등록 실패: {error_msg}"}
        else:
            error_msg = str(e)
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", {}).get("message", error_msg)
            except Exception:
                pass
            return {"error": f"❌ 테스트 케이스 등록 실패: {error_msg}"}
    except Exception as e:
        return {"error": f"❌ 오류 발생: {str(e)}"}


# ============================================================================
# 기존 MCP 도구: 테스트 케이스 생성 (기본)
# ============================================================================

@mcp.tool(name="qtest_create_test_case", description="테스트 케이스를 qTest에 등록합니다.")
async def qtest_create_test_case(
    name: Annotated[str, Field(description="테스트 케이스 이름")],
    description: Annotated[Optional[str], Field(description="테스트 케이스 설명", default="")] = "",
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    return await create_test_case(name, description, project_id)


# ============================================================================
# 테스트 스텝 자동 생성 함수
# ============================================================================

def generate_test_steps_from_description(description: str) -> List[Dict]:
    test_steps = []
    if not description:
        return test_steps

    lines = description.split('\n')
    numbered_pattern = r'^\s*(\d+)[\.\)]\s*(.+)$'
    bullet_pattern = r'^\s*[-•]\s*(.+)$'

    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = re.match(numbered_pattern, line)
        if match:
            step_text = match.group(2).strip()
            if ':' in step_text or '->' in step_text or '→' in step_text:
                parts = re.split(r'[:->→]', step_text, 1)
                step_description = parts[0].strip()
                expected_result = parts[1].strip() if len(parts) > 1 else "정상적으로 동작한다"
            else:
                step_description = step_text
                expected_result = "정상적으로 동작한다"
            test_steps.append({"description": step_description, "expected": expected_result})
            continue

        match = re.match(bullet_pattern, line)
        if match:
            step_text = match.group(1).strip()
            if ':' in step_text or '->' in step_text or '→' in step_text:
                parts = re.split(r'[:->→]', step_text, 1)
                step_description = parts[0].strip()
                expected_result = parts[1].strip() if len(parts) > 1 else "정상적으로 동작한다"
            else:
                step_description = step_text
                expected_result = "정상적으로 동작한다"
            test_steps.append({"description": step_description, "expected": expected_result})
            continue

    if not test_steps:
        sentences = description.replace('\n', ' ').split('.')
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]
        for sentence in sentences:
            if not sentence:
                continue
            step_description = sentence
            expected_result = "정상적으로 동작한다"
            if "하면" in sentence:
                parts = sentence.split("하면", 1)
                if len(parts) == 2:
                    step_description = parts[0].strip()
                    expected_result = parts[1].strip()
            elif "시" in sentence and "한다" in sentence:
                parts = re.split(r'시\s+', sentence, 1)
                if len(parts) == 2:
                    step_description = parts[0].strip() + "시"
                    expected_result = parts[1].strip()
            test_steps.append({"description": step_description, "expected": expected_result})

    if not test_steps:
        test_steps.append({"description": description, "expected": "기대한 결과가 나타난다"})
    return test_steps


# ============================================================================
# 기존 MCP 도구: 테스트 스텝 포함 TC 생성
# ============================================================================

@mcp.tool(name="qtest_create_test_case_with_steps", description="테스트 케이스 이름과 내용을 받아서 구체적인 테스트 케이스(테스트 스텝 포함)를 생성하고 qTest에 등록합니다.")
async def qtest_create_test_case_with_steps_tool(
    name: Annotated[str, Field(description="테스트 케이스 이름")],
    description: Annotated[Optional[str], Field(description="테스트 케이스 설명", default="")] = "",
    test_steps: Annotated[Optional[List[Dict]], Field(description="테스트 스텝 배열. 각 스텝은 description과 expected를 포함합니다. 미제공 시 자동 생성됩니다.", default=None)] = None,
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    if (test_steps is None or (isinstance(test_steps, list) and len(test_steps) == 0)) and description:
        test_steps = generate_test_steps_from_description(description)
    if test_steps is None:
        test_steps = []
    return await create_test_case_with_steps(name, description, test_steps, project_id)


# ============================================================================
# 기존 MCP 도구: 프롬프트 기반 TC 자동 생성
# ============================================================================

@mcp.tool(name="qtest_generate_and_create", description="테스트 케이스 이름과 내용을 받아서 구체적인 테스트 케이스를 생성하고 qTest에 등록합니다. 테스트 스텝을 자동으로 생성합니다.")
async def qtest_generate_and_create(
    name: Annotated[str, Field(description="테스트 케이스 이름")],
    content: Annotated[str, Field(description="테스트 케이스 내용/설명. 이 내용을 기반으로 테스트 스텝이 자동 생성됩니다.")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    test_steps = generate_test_steps_from_description(content)
    return await create_test_case_with_steps(name, content, test_steps, project_id)


# ############################################################################
#  [추가] qTest SaaS 기능
# ############################################################################

# ============================================================================
# 1. 프로젝트 (Projects)
# ============================================================================

@mcp.tool(name="qtest_list_projects", description="qTest의 모든 프로젝트 목록을 조회합니다.")
async def qtest_list_projects(
    page: Annotated[Optional[int], Field(description="페이지 번호 (1부터 시작)", default=1)] = 1,
    page_size: Annotated[Optional[int], Field(description="페이지당 항목 수 (최대 100)", default=25)] = 25
) -> dict:
    params = {}
    if page is not None:
        params["page"] = page
    if page_size is not None:
        params["pageSize"] = page_size
    data = await _api_request("GET", "/api/v3/projects", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        return {
            "success": True, "total": len(data),
            "projects": [{"id": p.get("id"), "name": p.get("name"), "description": p.get("description", ""), "status_id": p.get("status_id"), "start_date": p.get("start_date"), "end_date": p.get("end_date")} for p in data]
        }
    return {"success": True, "data": data}


@mcp.tool(name="qtest_get_project", description="프로젝트 ID로 qTest 프로젝트 상세 정보를 조회합니다.")
async def qtest_get_project(
    project_id: Annotated[str, Field(description="프로젝트 ID")]
) -> dict:
    data = await _api_request("GET", f"/api/v3/projects/{project_id}")
    if isinstance(data, dict) and "error" in data:
        return data
    return {"success": True, "project": data}


# ============================================================================
# 2. 요구사항 (Requirements)
# ============================================================================

@mcp.tool(name="qtest_list_requirements", description="qTest 프로젝트의 요구사항 목록을 조회합니다.")
async def qtest_list_requirements(
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    parent_id: Annotated[Optional[int], Field(description="상위 모듈 ID", default=None)] = None,
    page: Annotated[Optional[int], Field(description="페이지 번호", default=1)] = 1,
    size: Annotated[Optional[int], Field(description="페이지당 항목 수 (최대 100)", default=25)] = 25
) -> dict:
    pid = _resolve_project_id(project_id)
    params = {}
    if parent_id is not None: params["parentId"] = parent_id
    if page is not None: params["page"] = page
    if size is not None: params["size"] = size
    data = await _api_request("GET", f"/api/v3/projects/{pid}/requirements", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        return {"success": True, "total": len(data), "requirements": [{"id": r.get("id"), "pid": r.get("pid"), "name": r.get("name"), "description": r.get("description", "")} for r in data]}
    return {"success": True, "data": data}


@mcp.tool(name="qtest_get_requirement", description="특정 요구사항의 상세 정보를 조회합니다.")
async def qtest_get_requirement(
    requirement_id: Annotated[int, Field(description="요구사항 ID")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    data = await _api_request("GET", f"/api/v3/projects/{pid}/requirements/{requirement_id}")
    if isinstance(data, dict) and "error" in data:
        return data
    return {"success": True, "requirement": data}


@mcp.tool(name="qtest_list_testcases_for_requirements", description="하나 이상의 요구사항에 연결된 테스트 케이스 목록을 조회합니다.")
async def qtest_list_testcases_for_requirements(
    requirement_ids: Annotated[List[int], Field(description="요구사항 ID 배열 (하나 이상)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    all_testcases = {}
    for req_id in requirement_ids:
        data = await _api_request("GET", f"/api/v3/projects/{pid}/linked-artifacts", params={"type": "test-cases", "ids": str(req_id), "sourceType": "requirements"})
        if isinstance(data, list):
            for tc in data:
                tc_id = tc.get("id")
                if tc_id and tc_id not in all_testcases:
                    all_testcases[tc_id] = tc
        elif isinstance(data, dict) and "items" in data:
            for tc in data["items"]:
                tc_id = tc.get("id")
                if tc_id and tc_id not in all_testcases:
                    all_testcases[tc_id] = tc
    return {"success": True, "total": len(all_testcases), "testcases": list(all_testcases.values())}


@mcp.tool(name="qtest_link_testcases_to_requirement", description="하나 이상의 테스트 케이스를 특정 요구사항에 연결(링크)합니다.")
async def qtest_link_testcases_to_requirement(
    requirement_id: Annotated[int, Field(description="요구사항 ID")],
    testcase_ids: Annotated[List[int], Field(description="연결할 테스트 케이스 ID 배열")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    payload = testcase_ids
    data = await _api_request("POST", f"/api/v3/projects/{pid}/requirements/{requirement_id}/link", payload=payload)
    if isinstance(data, dict) and "error" in data:
        link_payload = [{"objectId": tc_id, "type": "test-cases"} for tc_id in testcase_ids]
        data = await _api_request("POST", f"/api/v3/projects/{pid}/requirements/{requirement_id}/link", payload=link_payload)
        if isinstance(data, dict) and "error" in data:
            return data
    return {"success": True, "message": f"✅ {len(testcase_ids)}개의 테스트 케이스가 요구사항(ID: {requirement_id})에 연결되었습니다.", "requirement_id": requirement_id, "linked_testcase_ids": testcase_ids}


# ============================================================================
# 3. 모듈 (Modules)
# ============================================================================

@mcp.tool(name="qtest_search_modules", description="qTest 프로젝트에서 모듈을 이름으로 검색합니다.")
async def qtest_search_modules(
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    search: Annotated[Optional[str], Field(description="검색할 모듈 이름", default=None)] = None,
    parent_id: Annotated[Optional[int], Field(description="상위 모듈 ID", default=None)] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    params = {}
    if search: params["search"] = search
    if parent_id is not None: params["parentId"] = parent_id
    data = await _api_request("GET", f"/api/v3/projects/{pid}/modules", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        results = data
        if search:
            filtered = [m for m in results if search in m.get("name", "")]
            if filtered:
                results = filtered
        return {"success": True, "total": len(results), "modules": [{"id": m.get("id"), "name": m.get("name"), "description": m.get("description", ""), "pid": m.get("pid"), "parent_id": m.get("parent_id")} for m in results]}
    return {"success": True, "data": data}


@mcp.tool(name="qtest_create_module", description="qTest 프로젝트에 새 모듈을 생성합니다.")
async def qtest_create_module(
    name: Annotated[str, Field(description="모듈 이름")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    description: Annotated[Optional[str], Field(description="모듈 설명", default="")] = "",
    parent_id: Annotated[Optional[int], Field(description="상위 모듈 ID", default=None)] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    payload = {"name": name}
    if description: payload["description"] = description
    params = {}
    if parent_id is not None: params["parentId"] = parent_id
    data = await _api_request("POST", f"/api/v3/projects/{pid}/modules", payload=payload, params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    return {"success": True, "message": f"✅ 모듈 '{name}'이(가) 생성되었습니다. (ID: {data.get('id')})", "module": data}


# ============================================================================
# 4. 테스트 케이스 조회 / 수정
# ============================================================================

@mcp.tool(name="qtest_list_testcases", description="qTest 프로젝트의 테스트 케이스 목록을 조회합니다.")
async def qtest_list_testcases(
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    parent_id: Annotated[Optional[int], Field(description="모듈 ID", default=None)] = None,
    page: Annotated[Optional[int], Field(description="페이지 번호", default=1)] = 1,
    size: Annotated[Optional[int], Field(description="페이지당 항목 수 (최대 100)", default=25)] = 25
) -> dict:
    pid = _resolve_project_id(project_id)
    params = {}
    if parent_id is not None: params["parentId"] = parent_id
    if page is not None: params["page"] = page
    if size is not None: params["size"] = size
    data = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        return {"success": True, "total": len(data), "testcases": [{"id": tc.get("id"), "pid": tc.get("pid"), "name": tc.get("name"), "description": tc.get("description", ""), "precondition": tc.get("precondition", ""), "test_steps_count": len(tc.get("test_steps", []))} for tc in data]}
    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        return {"success": True, "total": data.get("total", len(items)), "page": data.get("page"), "page_size": data.get("pageSize"), "testcases": [{"id": tc.get("id"), "pid": tc.get("pid"), "name": tc.get("name"), "description": tc.get("description", "")} for tc in items]}
    return {"success": True, "data": data}


@mcp.tool(name="qtest_get_testcase", description="특정 테스트 케이스의 상세 정보를 조회합니다. (테스트 스텝 포함 가능)")
async def qtest_get_testcase(
    testcase_id: Annotated[int, Field(description="테스트 케이스 ID")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    expand: Annotated[Optional[str], Field(description="'teststep' 입력 시 테스트 스텝도 함께 조회", default=None)] = None,
    version_id: Annotated[Optional[int], Field(description="테스트 케이스 버전 ID", default=None)] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    params = {}
    if expand: params["expand"] = expand
    if version_id is not None: params["versionId"] = version_id
    data = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    return {"success": True, "testcase": data}


@mcp.tool(name="qtest_update_testcase", description="기존 테스트 케이스를 수정합니다. (이름, 설명, 사전조건, 테스트 스텝 등)")
async def qtest_update_testcase(
    testcase_id: Annotated[int, Field(description="테스트 케이스 ID")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    name: Annotated[Optional[str], Field(description="수정할 이름", default=None)] = None,
    description: Annotated[Optional[str], Field(description="수정할 설명", default=None)] = None,
    precondition: Annotated[Optional[str], Field(description="수정할 사전조건", default=None)] = None,
    parent_id: Annotated[Optional[int], Field(description="이동할 모듈 ID", default=None)] = None,
    test_steps: Annotated[Optional[List[Dict]], Field(description="수정할 테스트 스텝 배열 (description, expected 포함)", default=None)] = None,
    automation_content: Annotated[Optional[str], Field(description="Automation Content 값 (예: 'test_module#test_method'). 자동화 테스트 매핑에 사용됩니다.", default=None)] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    existing = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}")
    if isinstance(existing, dict) and "error" in existing:
        return existing

    payload = {}
    payload["name"] = name if name is not None else existing.get("name", "")
    if description is not None: payload["description"] = description
    if precondition is not None: payload["precondition"] = precondition
    if parent_id is not None: payload["parent_id"] = parent_id
    if test_steps is not None:
        payload["test_steps"] = [{"description": s.get("description", ""), "expected": s.get("expected_result", s.get("expected", "")), "order": i + 1} for i, s in enumerate(test_steps)]

    # Automation Content property 업데이트
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

    data = await _api_request("PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}", payload=payload)
    if isinstance(data, dict) and "error" in data:
        return data
    return {"success": True, "message": f"✅ 테스트 케이스(ID: {testcase_id})가 수정되었습니다.", "testcase": data}


@mcp.tool(
    name="qtest_update_testcase_property",
    description="테스트 케이스의 특정 Property(속성)를 업데이트합니다. Automation Content, Priority, Status 등 field_name으로 지정하여 값을 변경할 수 있습니다."
)
async def qtest_update_testcase_property(
    testcase_id: Annotated[int, Field(description="테스트 케이스 ID")],
    field_name: Annotated[str, Field(description="변경할 Property 이름 (예: 'Automation Content', 'Priority', 'Automation')")],
    field_value: Annotated[str, Field(description="변경할 값 (예: 'test_module#test_method', '723' 등)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    """테스트 케이스의 특정 Property를 field_name으로 찾아 업데이트합니다."""
    pid = _resolve_project_id(project_id)

    existing = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases/{testcase_id}")
    if isinstance(existing, dict) and "error" in existing:
        return existing

    # field_name으로 field_id 찾기
    existing_props = existing.get("properties", [])
    target_field_id = None
    for prop in existing_props:
        if prop.get("field_name", "").lower() == field_name.lower():
            target_field_id = prop.get("field_id")
            break

    if target_field_id is None:
        available = [p.get("field_name") for p in existing_props if p.get("field_name")]
        return {"error": f"❌ '{field_name}' Property를 찾을 수 없습니다.\n사용 가능: {', '.join(available)}"}

    payload = {
        "name": existing.get("name", ""),
        "properties": [{"field_id": target_field_id, "field_value": field_value}]
    }

    data = await _api_request("PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}", payload=payload)
    if isinstance(data, dict) and "error" in data:
        return data

    return {
        "success": True,
        "message": f"✅ 테스트 케이스(ID: {testcase_id})의 '{field_name}'이(가) '{field_value}'(으)로 변경되었습니다.",
        "testcase_id": testcase_id,
        "field_name": field_name,
        "field_value": field_value
    }


# ============================================================================
# 5. 테스트 런 / 테스트 로그
# ============================================================================

@mcp.tool(name="qtest_list_testruns_for_testcases", description="하나 이상의 테스트 케이스에 연결된 테스트 런 목록을 조회합니다.")
async def qtest_list_testruns_for_testcases(
    testcase_ids: Annotated[List[int], Field(description="테스트 케이스 ID 배열")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    all_runs = []
    for tc_id in testcase_ids:
        data = await _api_request("GET", f"/api/v3/projects/{pid}/test-runs", params={"parentId": tc_id, "parentType": "test-case"})
        if isinstance(data, list):
            all_runs.extend(data)
        elif isinstance(data, dict) and "items" in data:
            all_runs.extend(data["items"])
        else:
            alt_data = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases/{tc_id}/test-runs")
            if isinstance(alt_data, list):
                all_runs.extend(alt_data)
    return {"success": True, "total": len(all_runs), "test_runs": all_runs}


@mcp.tool(name="qtest_list_testlogs_for_testrun", description="특정 테스트 런의 테스트 로그(실행 기록) 목록을 조회합니다.")
async def qtest_list_testlogs_for_testrun(
    test_run_id: Annotated[int, Field(description="테스트 런 ID")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    page: Annotated[Optional[int], Field(description="페이지 번호", default=1)] = 1,
    page_size: Annotated[Optional[int], Field(description="페이지당 항목 수 (최대 100)", default=25)] = 25
) -> dict:
    pid = _resolve_project_id(project_id)
    params = {}
    if page is not None: params["page"] = page
    if page_size is not None: params["pageSize"] = page_size
    data = await _api_request("GET", f"/api/v3/projects/{pid}/test-runs/{test_run_id}/test-logs", params=params)
    if isinstance(data, dict) and "error" in data:
        return data
    if isinstance(data, list):
        return {"success": True, "total": len(data), "test_logs": data}
    if isinstance(data, dict) and "items" in data:
        return {"success": True, "total": data.get("total", len(data["items"])), "page": data.get("page"), "page_size": data.get("pageSize"), "test_logs": data["items"]}
    return {"success": True, "data": data}


# ============================================================================
# 6. 결함 (Defects)
# ============================================================================

@mcp.tool(name="qtest_create_defect", description="테스트 로그에 대한 결함(Defect)을 생성합니다.")
async def qtest_create_defect(
    test_log_id: Annotated[int, Field(description="테스트 로그 ID")],
    summary: Annotated[str, Field(description="결함 요약")],
    description: Annotated[str, Field(description="결함 상세 설명 (실제 결과 vs 예상 결과)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    pid = _resolve_project_id(project_id)
    payload = {"name": summary, "description": description, "test_logs": [test_log_id]}
    data = await _api_request("POST", f"/api/v3/projects/{pid}/defects", payload=payload)
    if isinstance(data, dict) and "error" in data:
        simple_payload = {"name": summary, "description": description}
        data = await _api_request("POST", f"/api/v3/projects/{pid}/defects", payload=simple_payload)
        if isinstance(data, dict) and "error" in data:
            return data
    return {"success": True, "message": f"✅ 결함이 생성되었습니다. (ID: {data.get('id')})", "defect": data}


# ============================================================================
# 7. 통합 검색 (Search Objects)
# ============================================================================

@mcp.tool(name="qtest_search_objects", description="qTest 프로젝트 내에서 다양한 객체(요구사항, 테스트케이스, 테스트런, 결함 등)를 검색합니다.")
async def qtest_search_objects(
    object_type: Annotated[str, Field(description="검색할 객체 유형: releases, requirements, test-cases, test-runs, test-suites, test-cycles, test-logs, builds, defects")],
    query: Annotated[str, Field(description="검색 쿼리")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    search_field: Annotated[Optional[str], Field(description="검색 대상 필드: 'id' 또는 'name' (기본: 'name')", default="name")] = "name",
    exact_match: Annotated[Optional[bool], Field(description="정확히 일치 검색 여부 (기본: False)", default=False)] = False,
    page: Annotated[Optional[int], Field(description="페이지 번호", default=1)] = 1,
    size: Annotated[Optional[int], Field(description="페이지당 항목 수 (최대 100)", default=25)] = 25
) -> dict:
    pid = _resolve_project_id(project_id)
    valid_types = ["releases", "requirements", "test-cases", "test-runs", "test-suites", "test-cycles", "test-logs", "builds", "defects"]
    if object_type not in valid_types:
        return {"error": f"지원되지 않는 객체 유형입니다. 지원 유형: {', '.join(valid_types)}"}

    search_payload = {"object_type": object_type, "fields": ["*"], "query": f"'{search_field}' ~ '{query}'" if not exact_match else f"'{search_field}' = '{query}'"}
    params = {}
    if page is not None: params["page"] = page
    if size is not None: params["pageSize"] = size

    data = await _api_request("POST", f"/api/v3/projects/{pid}/search", payload=search_payload, params=params)
    if isinstance(data, dict) and "error" in data:
        list_data = await _api_request("GET", f"/api/v3/projects/{pid}/{object_type}", params={"page": page, "size": size})
        if isinstance(list_data, list):
            if search_field == "name":
                filtered = [o for o in list_data if o.get("name") == query] if exact_match else [o for o in list_data if query.lower() in o.get("name", "").lower()]
            elif search_field == "id":
                filtered = [o for o in list_data if str(o.get("id")) == str(query)]
            else:
                filtered = list_data
            return {"success": True, "object_type": object_type, "total": len(filtered), "items": filtered}
        if isinstance(list_data, dict) and "error" in list_data:
            return list_data
        return {"success": True, "data": list_data}

    if isinstance(data, dict):
        items = data.get("items", data.get("results", []))
        return {"success": True, "object_type": object_type, "total": data.get("total", len(items)), "page": data.get("page"), "page_size": data.get("pageSize"), "items": items}
    if isinstance(data, list):
        return {"success": True, "object_type": object_type, "total": len(data), "items": data}
    return {"success": True, "data": data}


# ============================================================================
# 8. 테스트 케이스 Approve
# ============================================================================

@mcp.tool(name="qtest_approve_test_case", description="테스트 케이스를 승인(Approve)합니다. 승인된 테스트 케이스만 Test Execution에서 실행할 수 있습니다.")
async def qtest_approve_test_case(
    testcase_id: Annotated[int, Field(description="승인할 테스트 케이스 ID")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    """테스트 케이스 승인 - PUT /api/v3/projects/{projectId}/test-cases/{testCaseId}/approve"""
    pid = _resolve_project_id(project_id)
    data = await _api_request("PUT", f"/api/v3/projects/{pid}/test-cases/{testcase_id}/approve")
    if isinstance(data, dict) and "error" in data:
        return data
    return {
        "success": True,
        "message": f"✅ 테스트 케이스(ID: {testcase_id})가 승인(Approve)되었습니다.",
        "testcase": data
    }


@mcp.tool(name="qtest_approve_test_cases_bulk", description="여러 테스트 케이스를 한번에 승인(Approve)합니다.")
async def qtest_approve_test_cases_bulk(
    testcase_ids: Annotated[List[int], Field(description="승인할 테스트 케이스 ID 배열")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    """여러 테스트 케이스를 일괄 승인"""
    pid = _resolve_project_id(project_id)
    results = {"approved": [], "failed": []}
    for tc_id in testcase_ids:
        data = await _api_request("PUT", f"/api/v3/projects/{pid}/test-cases/{tc_id}/approve")
        if isinstance(data, dict) and "error" in data:
            results["failed"].append({"id": tc_id, "error": data["error"]})
        else:
            results["approved"].append(tc_id)
    return {
        "success": True,
        "message": f"✅ {len(results['approved'])}개 승인 완료, {len(results['failed'])}개 실패",
        "approved_ids": results["approved"],
        "failed": results["failed"]
    }


# ============================================================================
# 9. Test Cycle 생성
# ============================================================================

@mcp.tool(name="qtest_create_test_cycle", description="Test Execution에 새로운 Test Cycle을 생성합니다. Test Cycle은 Test Suite와 Test Run을 담는 상위 컨테이너입니다.")
async def qtest_create_test_cycle(
    name: Annotated[str, Field(description="Test Cycle 이름")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    description: Annotated[Optional[str], Field(description="Test Cycle 설명", default="")] = "",
    parent_id: Annotated[Optional[int], Field(description="상위 Release 또는 Test Cycle ID. 0이면 루트(root)에 생성", default=0)] = 0,
    parent_type: Annotated[Optional[str], Field(description="상위 컨테이너 유형: 'root', 'release', 'test-cycle' (기본: 'root')", default="root")] = "root"
) -> dict:
    """Test Cycle 생성 - POST /api/v3/projects/{projectId}/test-cycles"""
    pid = _resolve_project_id(project_id)
    payload = {"name": name}
    if description:
        payload["description"] = description

    params = {}
    if parent_id is not None and parent_id != 0:
        params["parentId"] = parent_id
    if parent_type:
        params["parentType"] = parent_type

    data = await _api_request("POST", f"/api/v3/projects/{pid}/test-cycles", payload=payload, params=params)
    if isinstance(data, dict) and "error" in data:
        return data

    web_url = data.get("web_url", "") or f"{QTEST_URL}/p/{pid}/portal/project#tab=testexecution&object=2&id={data.get('id')}"
    return {
        "success": True,
        "message": f"✅ Test Cycle '{name}'이(가) 생성되었습니다.\n\nID: {data.get('id')} | PID: {data.get('pid')}\nURL: {web_url}",
        "id": data.get("id"),
        "pid": data.get("pid"),
        "name": data.get("name"),
        "web_url": web_url,
        "test_cycle": data
    }


# ============================================================================
# 10. Test Suite 생성
# ============================================================================

@mcp.tool(name="qtest_create_test_suite", description="Test Execution에 새로운 Test Suite를 생성합니다. Test Suite는 Test Run을 그룹화하는 컨테이너이며, Release 또는 Test Cycle 하위에 생성할 수 있습니다.")
async def qtest_create_test_suite(
    name: Annotated[str, Field(description="Test Suite 이름")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    description: Annotated[Optional[str], Field(description="Test Suite 설명", default="")] = "",
    parent_id: Annotated[Optional[int], Field(description="상위 Release 또는 Test Cycle ID. 0이면 루트(root)에 생성", default=0)] = 0,
    parent_type: Annotated[Optional[str], Field(description="상위 컨테이너 유형: 'root', 'release', 'test-cycle' (기본: 'root')", default="root")] = "root"
) -> dict:
    """Test Suite 생성 - POST /api/v3/projects/{projectId}/test-suites"""
    pid = _resolve_project_id(project_id)
    payload = {"name": name}
    if description:
        payload["description"] = description

    params = {}
    if parent_id is not None and parent_id != 0:
        params["parentId"] = parent_id
    if parent_type:
        params["parentType"] = parent_type

    data = await _api_request("POST", f"/api/v3/projects/{pid}/test-suites", payload=payload, params=params)
    if isinstance(data, dict) and "error" in data:
        return data

    web_url = data.get("web_url", "") or f"{QTEST_URL}/p/{pid}/portal/project#tab=testexecution&object=5&id={data.get('id')}"
    return {
        "success": True,
        "message": f"✅ Test Suite '{name}'이(가) 생성되었습니다.\n\nID: {data.get('id')} | PID: {data.get('pid')}\nURL: {web_url}",
        "id": data.get("id"),
        "pid": data.get("pid"),
        "name": data.get("name"),
        "web_url": web_url,
        "test_suite": data
    }


# ============================================================================
# 11. Test Run 생성
# ============================================================================

@mcp.tool(name="qtest_create_test_run", description="Test Execution에 새로운 Test Run을 생성합니다. Test Run은 승인된 테스트 케이스의 실행 가능한 인스턴스입니다. Test Suite, Test Cycle, Release 하위에 생성할 수 있습니다.")
async def qtest_create_test_run(
    name: Annotated[str, Field(description="Test Run 이름")],
    test_case_id: Annotated[int, Field(description="연결할 테스트 케이스 ID (승인된 TC여야 함)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    description: Annotated[Optional[str], Field(description="Test Run 설명", default="")] = "",
    parent_id: Annotated[Optional[int], Field(description="상위 Test Suite, Test Cycle 또는 Release ID. 0이면 루트(root)에 생성", default=0)] = 0,
    parent_type: Annotated[Optional[str], Field(description="상위 컨테이너 유형: 'root', 'test-suite', 'test-cycle', 'release' (기본: 'root')", default="root")] = "root",
    test_case_version_id: Annotated[Optional[int], Field(description="테스트 케이스 버전 ID (미지정 시 최신 승인 버전 사용)", default=None)] = None
) -> dict:
    """Test Run 생성 - POST /api/v3/projects/{projectId}/test-runs"""
    pid = _resolve_project_id(project_id)

    payload = {"name": name}
    if description:
        payload["description"] = description

    # test_case 연결 정보
    test_case_info = {"id": test_case_id}
    if test_case_version_id is not None:
        test_case_info["test_case_version_id"] = test_case_version_id
    payload["test_case"] = test_case_info

    params = {}
    if parent_id is not None and parent_id != 0:
        params["parentId"] = parent_id
    if parent_type:
        params["parentType"] = parent_type

    data = await _api_request("POST", f"/api/v3/projects/{pid}/test-runs", payload=payload, params=params)
    if isinstance(data, dict) and "error" in data:
        return data

    web_url = data.get("web_url", "") or f"{QTEST_URL}/p/{pid}/portal/project#tab=testexecution&object=3&id={data.get('id')}"
    return {
        "success": True,
        "message": f"✅ Test Run '{name}'이(가) 생성되었습니다.\n\nID: {data.get('id')} | PID: {data.get('pid')}\nTest Case ID: {test_case_id}\nURL: {web_url}",
        "id": data.get("id"),
        "pid": data.get("pid"),
        "name": data.get("name"),
        "test_case_id": test_case_id,
        "web_url": web_url,
        "test_run": data
    }


@mcp.tool(name="qtest_create_test_runs_bulk", description="여러 테스트 케이스에 대한 Test Run을 한번에 생성합니다. 지정한 Test Suite 또는 Test Cycle 하위에 일괄 생성됩니다.")
async def qtest_create_test_runs_bulk(
    test_case_ids: Annotated[List[int], Field(description="Test Run을 생성할 테스트 케이스 ID 배열 (승인된 TC여야 함)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    parent_id: Annotated[Optional[int], Field(description="상위 Test Suite, Test Cycle 또는 Release ID. 0이면 루트(root)에 생성", default=0)] = 0,
    parent_type: Annotated[Optional[str], Field(description="상위 컨테이너 유형: 'root', 'test-suite', 'test-cycle', 'release' (기본: 'test-suite')", default="test-suite")] = "test-suite"
) -> dict:
    """여러 TC에 대한 Test Run 일괄 생성"""
    pid = _resolve_project_id(project_id)
    results = {"created": [], "failed": []}

    for tc_id in test_case_ids:
        # TC 정보를 가져와서 이름 확인
        tc_data = await _api_request("GET", f"/api/v3/projects/{pid}/test-cases/{tc_id}")
        tc_name = tc_data.get("name", f"TC-{tc_id}") if isinstance(tc_data, dict) and "error" not in tc_data else f"TC-{tc_id}"

        payload = {
            "name": f"{tc_name}",
            "test_case": {"id": tc_id}
        }
        params = {}
        if parent_id is not None and parent_id != 0:
            params["parentId"] = parent_id
        if parent_type:
            params["parentType"] = parent_type

        data = await _api_request("POST", f"/api/v3/projects/{pid}/test-runs", payload=payload, params=params)
        if isinstance(data, dict) and "error" in data:
            results["failed"].append({"test_case_id": tc_id, "error": data["error"]})
        else:
            results["created"].append({"test_case_id": tc_id, "test_run_id": data.get("id"), "name": data.get("name"), "pid": data.get("pid")})

    return {
        "success": True,
        "message": f"✅ {len(results['created'])}개 Test Run 생성 완료, {len(results['failed'])}개 실패",
        "created": results["created"],
        "failed": results["failed"]
    }


# ############################################################################
#  [추가] Automation Host / Agent / Schedule 관련 도구
# ############################################################################

# ============================================================================
# 12. Automation Hosts 조회
# ============================================================================

@mcp.tool(
    name="qtest_list_automation_hosts",
    description="qTest에 등록된 Automation Host 목록을 조회합니다. Host는 테스트 자동화가 실행되는 서버/머신입니다."
)
async def qtest_list_automation_hosts(
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    """Automation Host 목록 조회"""
    pid = _resolve_project_id(project_id)

    # 프로젝트별 호스트 조회
    data = await _api_request("GET", f"/api/v3/projects/{pid}/automation/hosts")

    if isinstance(data, dict) and "error" in data:
        # 대안: 전체 호스트 조회
        data = await _api_request("GET", "/api/v3/automation/hosts")
        if isinstance(data, dict) and "error" in data:
            return data

    if isinstance(data, list):
        return {
            "success": True,
            "total": len(data),
            "hosts": [
                {
                    "host_server_id": h.get("host_server_id", h.get("id")),
                    "host_name": h.get("host_name", h.get("name", "")),
                    "ip_address": h.get("ip_address", ""),
                    "mac_address": h.get("mac_address", ""),
                    "host_guid": h.get("host_guid", ""),
                    "state": h.get("state", ""),
                }
                for h in data
            ]
        }

    return {"success": True, "data": data}


# ============================================================================
# 13. Automation Agents 조회
# ============================================================================

@mcp.tool(
    name="qtest_list_automation_agents",
    description="특정 Automation Host에 등록된 Agent 목록을 조회합니다. Agent는 특정 테스트 프레임워크를 실행하는 단위입니다."
)
async def qtest_list_automation_agents(
    host_id: Annotated[int, Field(description="Automation Host ID (host_server_id)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None
) -> dict:
    """특정 Host의 Automation Agent 목록 조회"""
    pid = _resolve_project_id(project_id)

    data = await _api_request("GET", f"/api/v3/projects/{pid}/automation/hosts/{host_id}/agents")

    if isinstance(data, dict) and "error" in data:
        return data

    if isinstance(data, list):
        return {
            "success": True,
            "total": len(data),
            "host_id": host_id,
            "agents": [
                {
                    "agent_server_id": a.get("agent_server_id", a.get("id")),
                    "name": a.get("name", ""),
                    "framework": a.get("framework", ""),
                    "framework_id": a.get("framework_id", ""),
                    "active": a.get("active", False),
                    "project_id": a.get("project_id", ""),
                    "host_id": a.get("host_id", host_id),
                }
                for a in data
            ]
        }

    return {"success": True, "data": data}


@mcp.tool(
    name="qtest_search_automation_agents",
    description="프로젝트에서 사용 가능한 모든 Automation Agent를 조회합니다. (POST /api/v3/automation/automation-agents) 스케줄링에 사용할 Agent를 찾을 때 유용합니다."
)
async def qtest_search_automation_agents(
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    agent_name: Annotated[Optional[str], Field(description="Agent 이름으로 필터링 (선택사항)", default=None)] = None,
    framework: Annotated[Optional[str], Field(description="프레임워크로 필터링 (예: 'testNG', 'junit', 'universalAgent')", default=None)] = None,
    active_only: Annotated[Optional[bool], Field(description="활성 Agent만 조회 (기본: True)", default=True)] = True
) -> dict:
    """프로젝트의 모든 Automation Agent 검색"""
    pid = _resolve_project_id(project_id)

    # POST /api/v3/automation/automation-agents (Manager 10.5+)
    search_payload = {"project_ids": [int(pid)]}
    data = await _api_request("POST", "/api/v3/automation/automation-agents", payload=search_payload)

    if isinstance(data, dict) and "error" in data:
        # 대안: Host 목록을 먼저 가져온 후 각 Host의 Agent를 조회
        hosts_data = await _api_request("GET", f"/api/v3/projects/{pid}/automation/hosts")
        if isinstance(hosts_data, dict) and "error" in hosts_data:
            hosts_data = await _api_request("GET", "/api/v3/automation/hosts")

        all_agents = []
        if isinstance(hosts_data, list):
            for host in hosts_data:
                host_id = host.get("host_server_id", host.get("id"))
                if host_id:
                    agents_data = await _api_request("GET", f"/api/v3/projects/{pid}/automation/hosts/{host_id}/agents")
                    if isinstance(agents_data, list):
                        for a in agents_data:
                            a["_host_name"] = host.get("host_name", host.get("name", ""))
                            a["_host_id"] = host_id
                            a["_host_state"] = host.get("state", "")
                        all_agents.extend(agents_data)
        data = all_agents

    # 결과를 리스트로 정규화
    agents_list = []
    if isinstance(data, list):
        agents_list = data
    elif isinstance(data, dict) and "items" in data:
        agents_list = data["items"]

    # 필터링
    if active_only:
        agents_list = [a for a in agents_list if a.get("active", False)]
    if agent_name:
        agents_list = [a for a in agents_list if agent_name.lower() in a.get("name", "").lower()]
    if framework:
        agents_list = [a for a in agents_list if framework.lower() in a.get("framework", "").lower() or framework.lower() in a.get("framework_id", "").lower()]

    return {
        "success": True,
        "total": len(agents_list),
        "agents": [
            {
                "agent_server_id": a.get("agent_server_id", a.get("id")),
                "name": a.get("name", ""),
                "framework": a.get("framework", ""),
                "framework_id": a.get("framework_id", ""),
                "active": a.get("active", False),
                "project_id": a.get("project_id", ""),
                "host_id": a.get("host_id", a.get("_host_id", "")),
                "host_name": a.get("_host_name", ""),
                "host_state": a.get("_host_state", ""),
            }
            for a in agents_list
        ]
    }


# ============================================================================
# 14. Automation Schedule 생성 (즉시 실행)
# ============================================================================

@mcp.tool(
    name="qtest_create_automation_schedule",
    description="Automation Schedule을 생성하여 테스트를 즉시 실행합니다. Test Run ID 배열과 Agent ID를 지정하여 자동화 테스트를 스케줄링합니다."
)
async def qtest_create_automation_schedule(
    test_run_ids: Annotated[List[int], Field(description="실행할 Test Run ID 배열")],
    agent_id: Annotated[int, Field(description="실행할 Automation Agent ID (agent_server_id)")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    host_id: Annotated[Optional[int], Field(description="Automation Host ID (host_server_id, 선택사항)", default=None)] = None
) -> dict:
    """Automation Schedule 생성 - POST /api/v3/automation/jobs/schedule/create

    qTest Swagger 스펙 기준 camelCase payload:
      clientId, name, agentId, startDate, creator, projectId, testRunIds, dynamic
    """
    import datetime
    pid = _resolve_project_id(project_id)
    headers = await _get_headers()
    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # Swagger 스펙 기반 camelCase payload (실제 동작 확인됨)
    payload_camel = {
        "clientId": agent_id,
        "name": "MCP Schedule",
        "agentId": agent_id,
        "startDate": now_iso,
        "creator": 0,
        "projectId": int(pid),
        "testRunIds": test_run_ids,
        "dynamic": {}
    }

    # snake_case fallback payload
    payload_snake = {
        "project_id": int(pid),
        "test_run_ids": test_run_ids,
        "agent_id": agent_id,
    }
    if host_id is not None:
        payload_camel["hostId"] = host_id
        payload_snake["host_id"] = host_id

    # 시도할 (엔드포인트, payload) 조합 — camelCase 우선
    attempts = [
        (f"{QTEST_URL}/api/v3/automation/jobs/schedule/create", payload_camel),
        (f"{QTEST_URL}/api/v3/automation/schedules", payload_camel),
        (f"{QTEST_URL}/api/v3/automation/jobs/schedule/create", payload_camel),
        (f"{QTEST_URL}/api/v3/automation/jobs/schedule/create", payload_snake),
    ]

    last_error = ""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url, payload in attempts:
            try:
                json_body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                response = await client.post(url, headers=headers, content=json_body)
                response.raise_for_status()

                if response.status_code == 204 or not response.content:
                    data = {}
                else:
                    data = response.json()

                job_id = data if isinstance(data, int) else data.get("id", data.get("job_id", "")) if isinstance(data, dict) else data
                return {
                    "success": True,
                    "message": f"✅ Automation Schedule이 생성되었습니다.\n\nJob ID: {job_id}\nAgent ID: {agent_id}\nTest Run 수: {len(test_run_ids)}개\nEndpoint: {url}",
                    "job_id": job_id,
                    "agent_id": agent_id,
                    "test_run_ids": test_run_ids,
                    "data": data
                }
            except httpx.HTTPStatusError as e:
                error_msg = str(e)
                try:
                    err_body = e.response.json()
                    if isinstance(err_body, dict):
                        error_msg = err_body.get("message", err_body.get("error", {}).get("message", error_msg))
                except Exception:
                    pass
                last_error = f"[{url}] ({e.response.status_code}) {error_msg}"
                continue
            except Exception as e:
                last_error = f"[{url}] {str(e)}"
                continue

    return {"error": f"❌ 모든 엔드포인트 시도 실패. 마지막 에러: {last_error}"}


# ============================================================================
# 15. Automation Job 상태 조회
# ============================================================================

@mcp.tool(
    name="qtest_get_automation_jobs",
    description="Automation Host의 Job(스케줄) 목록과 상태를 조회합니다. 시작일/종료일로 필터링할 수 있습니다."
)
async def qtest_get_automation_jobs(
    host_id: Annotated[int, Field(description="Automation Host ID (host_server_id)")],
    start_date: Annotated[Optional[str], Field(description="시작일 (ISO 8601 형식, 예: '2026-01-01T00:00:00')", default=None)] = None,
    end_date: Annotated[Optional[str], Field(description="종료일 (ISO 8601 형식, 예: '2026-12-31T23:59:59')", default=None)] = None
) -> dict:
    """Automation Job 목록 조회 - GET /api/v3/automation/hosts/{hostId}/jobs"""
    params = {}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    data = await _api_request("GET", f"/api/v3/automation/hosts/{host_id}/jobs", params=params)

    if isinstance(data, dict) and "error" in data:
        return data

    if isinstance(data, list):
        return {
            "success": True,
            "total": len(data),
            "host_id": host_id,
            "jobs": [
                {
                    "id": j.get("id"),
                    "state": j.get("state", ""),
                    "status": j.get("status", ""),
                    "agent_id": j.get("agent_id", ""),
                    "agent_name": j.get("agent_name", ""),
                    "created_date": j.get("created_date", ""),
                    "last_modified_date": j.get("last_modified_date", ""),
                    "test_run_ids": j.get("test_run_ids", []),
                }
                for j in data
            ]
        }

    return {"success": True, "data": data}


# ============================================================================
# 16. Auto Test Log 제출 (테스트 실행 결과 기록)
# ============================================================================

@mcp.tool(
    name="qtest_submit_auto_test_log",
    description="Test Run에 자동화 테스트 실행 결과(Auto Test Log)를 제출합니다. 테스트를 PASSED/FAILED 등으로 기록할 수 있습니다."
)
async def qtest_submit_auto_test_log(
    test_run_id: Annotated[int, Field(description="테스트 런 ID")],
    status: Annotated[str, Field(description="실행 결과 상태 (예: 'PASSED', 'FAILED', 'INCOMPLETE', 'BLOCKED')")],
    project_id: Annotated[Optional[str], Field(description="프로젝트 ID (선택사항)")] = None,
    name: Annotated[Optional[str], Field(description="테스트 로그 이름 (미지정 시 Test Run 이름 사용)", default=None)] = None,
    note: Annotated[Optional[str], Field(description="실행 메모/노트", default="")] = "",
    automation_content: Annotated[Optional[str], Field(description="Automation Content 값", default="")] = "",
    test_step_logs: Annotated[Optional[List[Dict]], Field(description="테스트 스텝별 결과 배열 [{description, expected_result, actual_result, status}]", default=None)] = None
) -> dict:
    """Auto Test Log 제출 - POST /api/v3/projects/{projectId}/test-runs/{testRunId}/auto-test-logs

    이 API를 통해 Automation Schedule 없이도 직접 테스트 실행 결과를 기록할 수 있습니다.
    """
    import datetime
    pid = _resolve_project_id(project_id)
    now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # Test Run 이름 가져오기 (name 미지정 시)
    if not name:
        tr_data = await _api_request("GET", f"/api/v3/projects/{pid}/test-runs/{test_run_id}")
        if isinstance(tr_data, dict) and "error" not in tr_data:
            name = tr_data.get("name", f"TR-{test_run_id}")
        else:
            name = f"TR-{test_run_id}"

    payload = {
        "exe_start_date": now_iso,
        "exe_end_date": now_iso,
        "name": name,
        "status": status.upper(),
        "automation_content": automation_content or name,
        "note": note or "",
    }

    if test_step_logs:
        payload["test_step_logs"] = [
            {
                "description": s.get("description", ""),
                "expected_result": s.get("expected_result", s.get("expected", "")),
                "actual_result": s.get("actual_result", s.get("actual", "")),
                "status": s.get("status", status).upper(),
                "order": i
            }
            for i, s in enumerate(test_step_logs)
        ]

    data = await _api_request(
        "POST",
        f"/api/v3/projects/{pid}/test-runs/{test_run_id}/auto-test-logs",
        payload=payload
    )

    if isinstance(data, dict) and "error" in data:
        return data

    log_id = data.get("id", "") if isinstance(data, dict) else data
    web_url = f"{QTEST_URL}/p/{pid}/portal/project#tab=testexecution&object=3&id={test_run_id}"

    return {
        "success": True,
        "message": f"✅ 테스트 실행 결과가 기록되었습니다.\n\nTest Run ID: {test_run_id}\n결과: {status.upper()}\nLog ID: {log_id}\nURL: {web_url}",
        "test_run_id": test_run_id,
        "log_id": log_id,
        "status": status.upper(),
        "web_url": web_url,
        "data": data
    }


# ############################################################################
#  CLI 실행 (기존 코드 유지)
# ############################################################################

async def main():
    parser = argparse.ArgumentParser(description="qTest 테스트 케이스 등록")
    parser.add_argument("--name", "-n", help="테스트 케이스 이름")
    parser.add_argument("--description", "-d", default="", help="테스트 케이스 설명")
    parser.add_argument("--project-id", "-p", default=None, help="프로젝트 ID")
    args = parser.parse_args()
    name = args.name
    description = args.description

    if sys.platform == 'win32' and name:
        try:
            if '?' in name or any(ord(c) > 127 and ord(c) < 256 for c in name):
                try:
                    fixed = name.encode('latin1', errors='ignore').decode('cp949', errors='ignore')
                    if fixed and fixed != name and len(fixed) > 0:
                        name = fixed
                except Exception:
                    pass
        except Exception:
            pass

    if sys.platform == 'win32' and description:
        try:
            if '?' in description or any(ord(c) > 127 and ord(c) < 256 for c in description):
                try:
                    fixed = description.encode('latin1', errors='ignore').decode('cp949', errors='ignore')
                    if fixed and fixed != description and len(fixed) > 0:
                        description = fixed
                except Exception:
                    pass
        except Exception:
            pass

    if not name:
        parser.error("--name이 필요합니다.")

    print("=" * 50)
    print("qTest 테스트 케이스 등록")
    print("=" * 50)
    print(f"\n설정:\n  URL: {QTEST_URL}\n  프로젝트 ID: {args.project_id or DEFAULT_PROJECT_ID}")
    print(f"\n등록할 테스트 케이스:\n  이름: {name}\n  설명: {description or '(없음)'}")
    print("\n등록 중...\n" + "-" * 50)

    result = await create_test_case(name=name, description=description, project_id=args.project_id)
    if result.get("success"):
        print(result.get("message", ""))
    else:
        print(f"❌ 오류: {result.get('error', '알 수 없는 오류')}")
    print("=" * 50)
    return result


async def run_example():
    name = "로그인 테스트 (MCP 도구 테스트)"
    description = "사용자가 로그인하는 기능을 테스트합니다."
    test_steps = [
        {"description": "로그인 페이지에 접속한다", "expected": "로그인 페이지가 정상적으로 표시된다"},
        {"description": "이메일과 비밀번호를 입력한다", "expected": "입력한 값이 필드에 정상적으로 표시된다"},
        {"description": "로그인 버튼을 클릭한다", "expected": "로그인 처리가 진행되고 메인 페이지로 이동한다"}
    ]
    print("=" * 50)
    print("MCP 도구 테스트 - 테스트 스텝 포함")
    print("=" * 50)
    print(f"\n테스트 케이스 이름: {name}\n설명: {description}\n\n테스트 스텝 수: {len(test_steps)}개")
    for idx, step in enumerate(test_steps, 1):
        print(f"  {idx}. {step.get('description', '')}\n     예상 결과: {step.get('expected', '')}")
    print("\nqTest에 등록 중...\n" + "-" * 50)
    result = await create_test_case_with_steps(name=name, description=description, test_steps=test_steps)
    if result.get("success"):
        print(result.get("message", ""))
    else:
        print(f"❌ 오류: {result.get('error', '알 수 없는 오류')}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if "--run-example" in sys.argv or "-e" in sys.argv:
            asyncio.run(run_example())
        else:
            result = asyncio.run(main())
            sys.exit(0 if result.get("success") else 1)
    else:
        mcp.run(transport="http", host="127.0.0.1", port=8001)