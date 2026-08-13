"""Benchmark sanity tests: hybrid must beat the static baseline on recall."""
from benchmark.run import _aggregate, _metrics, run_benchmark
from benchmark.scenarios import generate_scenarios
from demo.ecosystem import build_full_graph, build_static_only_graph


def test_scenarios_are_deterministic():
    a = generate_scenarios(10, seed=1)
    b = generate_scenarios(10, seed=1)
    assert [s.id for s in a] == [s.id for s in b]
    assert [s.changed_services for s in a] == [s.changed_services for s in b]


def test_metrics_math():
    m = _metrics({"a", "b"}, {"b", "c"})
    assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 1
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_aggregate():
    agg = _aggregate([_metrics({"a"}, {"a"}), _metrics({"b"}, {"c"})])
    assert agg["tp"] == 1 and agg["fp"] == 1 and agg["fn"] == 1
    assert agg["recall"] == 0.5


def test_benchmark_hybrid_beats_static_on_recall():
    code = run_benchmark(cases=20, seed=7)
    assert code == 0  # exit 0 => recall improvement > 20%


def test_truth_sets_vary_across_services():
    full = build_full_graph()
    static = build_static_only_graph()
    from demo.ecosystem import truth_set

    t_full = truth_set(full, ["promo-service"])
    t_static = truth_set(static, ["promo-service"])
    assert "order-service" in t_full
    assert len(t_static) == 1  # only the seed itself