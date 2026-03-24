"""
qTest MCP 공통 유틸리티 함수
"""


def format_test_result(test_id: int, name: str, passed: bool, detail: str = "") -> dict:
    """테스트 결과를 표준 포맷으로 반환"""
    return {
        "id": test_id,
        "name": name,
        "status": "PASSED" if passed else "FAILED",
        "detail": detail,
    }


def truncate(text: str, max_length: int = 80) -> str:
    """텍스트를 최대 길이로 자름"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def print_summary(results: list[dict]) -> None:
    """테스트 결과 요약 출력"""
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASSED")
    failed = total - passed

    print(f"\n{'='*50}")
    print(f"Total: {total}  Passed: {passed}  Failed: {failed}")
    print(f"{'='*50}\n")
