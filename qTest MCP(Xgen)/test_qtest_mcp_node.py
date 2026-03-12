"""
qTest MCP Node - 31개 도구 전체 실행 테스트
"""

import sys
import json
from abc import ABC, abstractmethod
from unittest.mock import MagicMock

# ── editor.node_composer Mock ──────────────────────────────────
mock_node_composer = MagicMock()

class _MockNode(ABC):
    @abstractmethod
    def execute(self, *args, **kwargs): ...

mock_node_composer.Node = _MockNode
sys.modules["editor"] = MagicMock()
sys.modules["editor.node_composer"] = mock_node_composer
# ──────────────────────────────────────────────────────────────

from qtest_mcp_node import QTestMCP

QTEST_URL          = "https://plateer.qtestnet.com"
QTEST_TOKEN        = "cGxhdGVlcnxyeXVzdW5nZEBwbGF0ZWVyLmNvbToxODAzMDE0MjMwNTYzOjUxMjQ5OGQ3NGE1OWE1YjJlZjYxOWUyYmFjODVkOTRi"
DEFAULT_PROJECT_ID = "127369"

# ── 테스트에 사용할 기존 데이터 ID ─────────────────────────────
EXISTING_TC_ID      = 138223712   # [인증] 인증번호 오류 입력 후 재입력 성공 검증
EXISTING_REQ_ID     = 59662735    # 인증 실패 시 재시도 가능
EXISTING_MODULE_ID  = 68277854    # 사용자접근 및 인증
EXISTING_RUN_ID     = 478268473   # TC-1 로그인 테스트 실행
# ──────────────────────────────────────────────────────────────

# 런타임에 생성된 ID 저장 (쓰기 도구 체인)
created = {}

# 결과 누적
log: list[tuple] = []   # (번호, 도구명, PASS/FAIL, 요약, 상세)

def call(tool_map, name: str, args: dict):
    """도구 호출 후 (ok, result_str) 반환"""
    try:
        result = str(tool_map[name].invoke(args))
        ok = "ERROR" not in result
        return ok, result
    except Exception as e:
        return False, str(e)

def rec(num, name, ok, summary, detail=""):
    icon = "✅" if ok else "❌"
    status = "PASS" if ok else "FAIL"
    log.append((num, name, status, summary))
    print(f"  {icon} [{num:02d}] {name:<45}  {summary}")
    if detail and not ok:
        for line in detail.splitlines()[:2]:
            print(f"          {line}")


def print_header(title):
    print(f"\n{'─'*70}")
    print(f"  {title}")
    print(f"{'─'*70}")


# ==============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  qTest MCP Node - 28개 도구 전체 실행 테스트")
    print(f"  URL: {QTEST_URL}  |  Project: {DEFAULT_PROJECT_ID}")
    print("=" * 70)

    # 도구 목록 생성
    node = QTestMCP()
    result = node.execute(
        qtest_url=QTEST_URL,
        qtest_token=QTEST_TOKEN,
        default_project_id=DEFAULT_PROJECT_ID,
    )
    tools = result["tools"]
    tm = {t.name: t for t in tools}
    print(f"\n  도구 {len(tools)}개 생성 완료\n")


    # ============================================================
    print_header("[ 1 / 5 ]  프로젝트 · 검색")
    # ============================================================

    ok, r = call(tm, "qtest_list_projects", {"page": 1, "page_size": 5})
    rec(1, "qtest_list_projects", ok, r.splitlines()[0] if ok else r[:80], r)

    ok, r = call(tm, "qtest_get_project", {"project_id": DEFAULT_PROJECT_ID})
    name = json.loads(r).get("name","?") if ok else "?"
    rec(2, "qtest_get_project", ok, f"name={name}", r)

    ok, r = call(tm, "qtest_search_objects",
                 {"object_type": "test-cases", "query": "로그인", "size": 3})
    rec(3, "qtest_search_objects (test-cases)", ok, r.splitlines()[0] if ok else r[:80], r)

    ok, r = call(tm, "qtest_search_objects",
                 {"object_type": "requirements", "query": "인증", "size": 3})
    rec(4, "qtest_search_objects (requirements)", ok, r.splitlines()[0] if ok else r[:80], r)

    ok, r = call(tm, "qtest_search_objects",
                 {"object_type": "test-runs", "query": "TC", "size": 3})
    rec(5, "qtest_search_objects (test-runs)", ok, r.splitlines()[0] if ok else r[:80], r)


    # ============================================================
    print_header("[ 2 / 5 ]  모듈 · 요구사항")
    # ============================================================

    ok, r = call(tm, "qtest_search_modules", {})
    rec(6, "qtest_search_modules", ok, r.splitlines()[0] if ok else r[:80], r)

    ok, r = call(tm, "qtest_create_module",
                 {"name": "[MCP Test] 자동생성 모듈", "description": "테스트용"})
    created["module_id"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["module_id"] = int(line.split("ID:")[-1].strip().rstrip(")"))
                except Exception:
                    pass
    rec(7, "qtest_create_module", ok,
        f"module_id={created.get('module_id')}" if ok else r[:80], r)

    ok, r = call(tm, "qtest_list_requirements", {"page": 1, "size": 5})
    rec(8, "qtest_list_requirements", ok, r.splitlines()[0] if ok else r[:80], r)

    ok, r = call(tm, "qtest_get_requirement", {"requirement_id": EXISTING_REQ_ID})
    req_name = json.loads(r).get("name","?") if ok else "?"
    rec(9, "qtest_get_requirement", ok, f"name={req_name}", r)

    ok, r = call(tm, "qtest_list_testcases_for_requirements",
                 {"requirement_ids": [EXISTING_REQ_ID]})
    rec(10, "qtest_list_testcases_for_requirements", ok, r.splitlines()[0] if ok else r[:80], r)


    # ============================================================
    print_header("[ 3 / 5 ]  테스트 케이스 CRUD")
    # ============================================================

    # 11. TC 생성 (기본)
    ok, r = call(tm, "qtest_create_test_case",
                 {"name": "[MCP Test] 기본 TC", "description": "자동 생성 테스트"})
    created["tc_basic"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["tc_basic"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(11, "qtest_create_test_case", ok,
        f"tc_id={created.get('tc_basic')}" if ok else r[:80], r)

    # 12. TC 생성 (스텝 포함)
    steps = [
        {"description": "로그인 페이지 접속", "expected": "페이지 로드됨"},
        {"description": "ID/PW 입력 후 로그인", "expected": "메인 화면 이동"},
    ]
    ok, r = call(tm, "qtest_create_test_case_with_steps",
                 {"name": "[MCP Test] 스텝 포함 TC", "test_steps": steps})
    created["tc_steps"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["tc_steps"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(12, "qtest_create_test_case_with_steps", ok,
        f"tc_id={created.get('tc_steps')}" if ok else r[:80], r)

    # 13. TC 자동 생성
    ok, r = call(tm, "qtest_generate_and_create",
                 {"name": "[MCP Test] 자동생성 TC",
                  "content": "1. 상품 페이지 이동\n2. 상품 선택\n3. 결제 완료"})
    created["tc_auto"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["tc_auto"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(13, "qtest_generate_and_create", ok,
        f"tc_id={created.get('tc_auto')}" if ok else r[:80], r)

    # 14. TC 목록
    ok, r = call(tm, "qtest_list_testcases", {"page": 1, "size": 5})
    rec(14, "qtest_list_testcases", ok, r.splitlines()[0] if ok else r[:80], r)

    # 15. TC 상세 (스텝 포함)
    ok, r = call(tm, "qtest_get_testcase",
                 {"testcase_id": EXISTING_TC_ID, "expand": "teststep"})
    tc_name = json.loads(r).get("name","?") if ok else "?"
    rec(15, "qtest_get_testcase", ok, f"name={tc_name}", r)

    # 16. TC 수정
    update_tc_id = created.get("tc_basic") or EXISTING_TC_ID
    ok, r = call(tm, "qtest_update_testcase",
                 {"testcase_id": update_tc_id,
                  "description": "MCP 테스트로 수정된 설명"})
    rec(16, "qtest_update_testcase", ok,
        f"tc_id={update_tc_id} 수정됨" if ok else r[:80], r)

    # 17. TC Property 업데이트
    # Automation=Yes(711) 단독 설정은 qTest가 Automation Content 동시 필요 → Content 필드 테스트
    ok, r = call(tm, "qtest_update_testcase_property",
                 {"testcase_id": update_tc_id,
                  "field_name": "Automation Content",
                  "field_value": "test_module#test_method"})
    rec(17, "qtest_update_testcase_property", ok,
        r.split("\n")[0] if ok else r[:80], r)

    # 18. TC 승인 (단건)
    approve_tc_id = created.get("tc_basic") or EXISTING_TC_ID
    ok, r = call(tm, "qtest_approve_test_case", {"testcase_id": approve_tc_id})
    rec(18, "qtest_approve_test_case", ok,
        f"tc_id={approve_tc_id} 승인됨" if ok else r[:80], r)

    # 19. TC 일괄 승인
    bulk_ids = [x for x in [created.get("tc_steps"), created.get("tc_auto")] if x]
    if not bulk_ids:
        bulk_ids = [EXISTING_TC_ID]
    ok, r = call(tm, "qtest_approve_test_cases_bulk", {"testcase_ids": bulk_ids})
    rec(19, "qtest_approve_test_cases_bulk", ok,
        r.splitlines()[0] if ok else r[:80], r)

    # 20. TC → 요구사항 연결
    link_tc_id = created.get("tc_basic") or EXISTING_TC_ID
    ok, r = call(tm, "qtest_link_testcases_to_requirement",
                 {"requirement_id": EXISTING_REQ_ID, "testcase_ids": [link_tc_id]})
    rec(20, "qtest_link_testcases_to_requirement", ok,
        r.split("\n")[0] if ok else r[:80], r)


    # ============================================================
    print_header("[ 4 / 5 ]  Test Execution (Cycle · Suite · Run · Log)")
    # ============================================================

    # 21. Test Cycle 생성
    ok, r = call(tm, "qtest_create_test_cycle",
                 {"name": "[MCP Test] 자동화 사이클"})
    created["cycle_id"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["cycle_id"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(21, "qtest_create_test_cycle", ok,
        f"cycle_id={created.get('cycle_id')}" if ok else r[:80], r)

    # 22. Test Suite 생성 (Cycle 하위)
    suite_args = {"name": "[MCP Test] 자동화 Suite"}
    if created.get("cycle_id"):
        suite_args.update({"parent_id": created["cycle_id"], "parent_type": "test-cycle"})
    ok, r = call(tm, "qtest_create_test_suite", suite_args)
    created["suite_id"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["suite_id"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(22, "qtest_create_test_suite", ok,
        f"suite_id={created.get('suite_id')}" if ok else r[:80], r)

    # 23. Test Run 생성 (Suite 하위, 승인된 TC 사용)
    run_tc_id = created.get("tc_basic") or EXISTING_TC_ID
    run_args = {"name": "[MCP Test] 자동화 Run", "test_case_id": run_tc_id}
    if created.get("suite_id"):
        run_args.update({"parent_id": created["suite_id"], "parent_type": "test-suite"})
    ok, r = call(tm, "qtest_create_test_run", run_args)
    created["run_id"] = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    created["run_id"] = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(23, "qtest_create_test_run", ok,
        f"run_id={created.get('run_id')}" if ok else r[:80], r)

    # 24. Test Run 일괄 생성
    bulk_tc_ids = [x for x in [created.get("tc_steps"), created.get("tc_auto")] if x]
    if not bulk_tc_ids:
        bulk_tc_ids = [EXISTING_TC_ID]
    bulk_run_args = {"test_case_ids": bulk_tc_ids}
    if created.get("suite_id"):
        bulk_run_args.update({"parent_id": created["suite_id"], "parent_type": "test-suite"})
    ok, r = call(tm, "qtest_create_test_runs_bulk", bulk_run_args)
    rec(24, "qtest_create_test_runs_bulk", ok,
        r.splitlines()[0] if ok else r[:80], r)

    # 25. TC에 연결된 Test Run 목록
    ok, r = call(tm, "qtest_list_testruns_for_testcases",
                 {"testcase_ids": [run_tc_id]})
    rec(25, "qtest_list_testruns_for_testcases", ok,
        r.splitlines()[0] if ok else r[:80], r)

    # 26. Auto Test Log 제출 (PASSED) - 자동화 설정된 기존 Run 사용
    submit_run_id = EXISTING_RUN_ID
    ok, r = call(tm, "qtest_submit_auto_test_log",
                 {"test_run_id": submit_run_id,
                  "status": "PASSED",
                  "note": "MCP 자동 테스트 결과"})
    rec(26, "qtest_submit_auto_test_log", ok,
        r.split("\n")[0] if ok else r[:80], r)

    # 27. Test Log 목록 조회
    ok, r = call(tm, "qtest_list_testlogs_for_testrun",
                 {"test_run_id": submit_run_id})
    rec(27, "qtest_list_testlogs_for_testrun", ok,
        r.splitlines()[0] if ok else r[:80], r)


    # ============================================================
    print_header("[ 5 / 5 ]  Automation Agent · Schedule · Job")
    # ============================================================

    ok, r = call(tm, "qtest_search_automation_agents", {"active_only": False})
    agent_id = None
    if ok:
        for line in r.splitlines():
            if "ID:" in line:
                try:
                    agent_id = int(line.split("ID:")[1].split("|")[0].strip())
                    break
                except Exception:
                    pass
    rec(28, "qtest_search_automation_agents", ok,
        f"agent_id={agent_id}" if ok else r[:80], r)



    # ============================================================
    # 최종 요약표
    # ============================================================
    passed = sum(1 for _, _, s, _ in log if s == "PASS")
    failed = sum(1 for _, _, s, _ in log if s == "FAIL")

    print(f"\n{'='*70}")
    print(f"  최종 결과 요약  ({passed} PASS / {failed} FAIL / 총 {len(log)}개)")
    print(f"{'='*70}")
    print(f"  {'#':>3}  {'도구명':<45}  {'결과'}")
    print(f"  {'─'*3}  {'─'*45}  {'─'*6}")
    for num, name, status, summary in log:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {num:>3}  {name:<45}  {icon} {status}")
    print(f"{'='*70}")
    print(f"  생성된 데이터 (테스트 후 확인 가능)")
    for k, v in created.items():
        if v:
            print(f"    {k}: {v}")
    print(f"{'='*70}\n")
