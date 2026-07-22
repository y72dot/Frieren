from scripts.benchmark import benchmark


def test_performance_harness_smoke():
    result = benchmark(messages=50, searches=10)
    assert result["messages"] == 50
    assert result["searches"] == 10
    assert result["ingest_messages_per_second"] > 0
    assert result["search_p95_ms"] >= 0
