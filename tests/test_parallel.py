"""Tests for the parallelism settings (almanac.parallel); no database needed."""

import logging
import sys
import threading
import types

import pytest
from click.testing import CliRunner

from almanac import parallel
from almanac.parallel import (
    cpu_budget,
    resolve_processes,
    resolve_query_workers,
)


class TestCpuBudget:
    def test_slurm_cpus_on_node_wins_over_cpu_count(self):
        n, source = cpu_budget({"SLURM_CPUS_ON_NODE": "12"}, affinity=None, cpu_count=128)
        assert n == 12
        assert "SLURM_CPUS_ON_NODE=12" in source

    def test_slurm_cpus_on_node_wins_over_larger_affinity(self):
        n, _ = cpu_budget({"SLURM_CPUS_ON_NODE": "12"}, affinity=32, cpu_count=128)
        assert n == 12

    def test_smaller_affinity_caps_slurm(self):
        # e.g. an srun step pinned to fewer CPUs than the job has on the node
        n, source = cpu_budget({"SLURM_CPUS_ON_NODE": "96"}, affinity=4, cpu_count=96)
        assert n == 4
        assert "affinity" in source

    def test_slurm_cpus_per_task_alone_is_not_used(self):
        # The pipeline scripts read SLURM_CPUS_ON_NODE only.
        n, source = cpu_budget({"SLURM_CPUS_PER_TASK": "3"}, affinity=8, cpu_count=128)
        assert n == 8
        assert "affinity" in source

    @pytest.mark.parametrize("bad", ["", "0", "-4", "abc", "48(x2)"])
    def test_invalid_slurm_value_falls_back_to_affinity(self, bad):
        n, _ = cpu_budget({"SLURM_CPUS_ON_NODE": bad}, affinity=6, cpu_count=128)
        assert n == 6

    def test_affinity_fallback(self):
        n, source = cpu_budget({}, affinity=7, cpu_count=128)
        assert (n, "affinity" in source) == (7, True)

    def test_cpu_count_fallback(self):
        n, source = cpu_budget({}, affinity=None, cpu_count=5)
        assert n == 5
        assert "cpu_count" in source

    def test_cpu_count_none(self):
        assert cpu_budget({}, affinity=None, cpu_count=None)[0] == 1

    def test_reads_os_environ_by_default(self, monkeypatch):
        monkeypatch.setenv("SLURM_CPUS_ON_NODE", "3")
        n, _ = cpu_budget(affinity=None, cpu_count=64)
        assert n == 3


class TestResolveProcesses:
    env = {"SLURM_CPUS_ON_NODE": "16"}

    def test_default_is_budget(self):
        n, source = resolve_processes(None, environ=self.env, affinity=None)
        assert n == 16
        assert "SLURM_CPUS_ON_NODE" in source

    def test_explicit_wins(self):
        # `-p 12` as in the pipeline scripts keeps working unchanged
        n, source = resolve_processes(12, environ=self.env, affinity=None)
        assert (n, source) == (12, "--processes")

    def test_explicit_may_exceed_budget(self):
        assert resolve_processes(40, environ=self.env, affinity=None)[0] == 40

    def test_negative_means_budget(self):
        n, source = resolve_processes(-1, environ=self.env, affinity=None)
        assert n == 16
        assert source.startswith("--processes -1 ->")

    def test_zero_means_serial(self):
        assert resolve_processes(0, environ=self.env, affinity=None)[0] == 1

    def test_capped_at_tasks(self):
        n, source = resolve_processes(None, n_tasks=1, environ=self.env, affinity=None)
        assert n == 1
        assert "capped" in source
        assert resolve_processes(12, n_tasks=3)[0] == 3

    def test_zero_tasks(self):
        assert resolve_processes(12, n_tasks=0)[0] == 1


class TestResolveQueryWorkers:
    def test_config_default(self):
        cfg = types.SimpleNamespace(catalog_query_max_workers=3)
        n, source = resolve_query_workers(None, config=cfg)
        assert n == 3
        assert "catalog_query_max_workers=3" in source

    def test_missing_config_key(self):
        n, _ = resolve_query_workers(None, config=types.SimpleNamespace())
        assert n == parallel.DEFAULT_QUERY_WORKERS

    def test_explicit_wins(self):
        cfg = types.SimpleNamespace(catalog_query_max_workers=3)
        assert resolve_query_workers(8, config=cfg) == (8, "--query-workers")

    def test_zero(self):
        cfg = types.SimpleNamespace(catalog_query_max_workers=3)
        assert resolve_query_workers(0, config=cfg, allow_zero=True)[0] == 0
        assert resolve_query_workers(0, config=cfg)[0] == 1


class TestMetadataOptions:
    def test_cli_uses_query_workers(self, tmp_path, monkeypatch):
        import almanac.metadata as md
        from almanac.cli import main

        seen = {}
        monkeypatch.setattr(md, "build_metadata", lambda input_path, **kw: seen.update(kw) or {})
        r = CliRunner().invoke(
            main, ["add", "metadata", str(tmp_path / "x.h5"), "--query-workers", "2"],
            catch_exceptions=False,
        )
        assert r.exit_code == 0
        assert seen["query_workers"] == 2

    def test_cli_default_query_workers(self, tmp_path, monkeypatch):
        import almanac.metadata as md
        from almanac.cli import main

        seen = {}
        monkeypatch.setattr(md, "build_metadata", lambda input_path, **kw: seen.update(kw) or {})
        r = CliRunner().invoke(main, ["add", "metadata", str(tmp_path / "x.h5")],
                               catch_exceptions=False)
        assert r.exit_code == 0
        assert seen["query_workers"] >= 1

    def test_removed_p_is_rejected(self, tmp_path, monkeypatch):
        import almanac.metadata as md
        from almanac.cli import main

        called = []
        monkeypatch.setattr(md, "build_metadata", lambda *a, **kw: called.append(1) or {})
        r = CliRunner().invoke(main, ["add", "metadata", str(tmp_path / "x.h5"), "--p", "4"])
        assert r.exit_code == 2  # click usage error
        assert "No such option" in r.output and "--p" in r.output
        assert not called


class TestDbGate:
    def teardown_method(self):
        parallel.install_db_gate(None)

    def test_no_gate_is_noop(self):
        parallel.acquire_db_slot()
        parallel.release_db_slot()

    def test_slot_taken_once_and_released(self):
        gate = threading.BoundedSemaphore(1)
        parallel.install_db_gate(gate)
        parallel.acquire_db_slot()
        parallel.acquire_db_slot()  # re-entrant within a task: no deadlock
        assert not gate.acquire(blocking=False)
        parallel.release_db_slot()
        assert gate.acquire(blocking=False)
        gate.release()

    def test_release_closes_connection(self, monkeypatch):
        closed = []
        fake = types.ModuleType("almanac.database")
        fake.database = types.SimpleNamespace(close=lambda: closed.append(True))
        monkeypatch.setitem(sys.modules, "almanac.database", fake)
        parallel.install_db_gate(threading.BoundedSemaphore(1))
        parallel.acquire_db_slot()
        parallel.release_db_slot()
        assert closed == [True]

    def test_retry_wrapper_takes_slot(self):
        from almanac.retry import retry_on_database_error

        gate = threading.BoundedSemaphore(2)
        parallel.install_db_gate(gate)

        @retry_on_database_error
        def query():
            return "ok"

        assert parallel.run_task_releasing_db_slot(query) == "ok"
        # one slot was taken during the task and given back afterwards
        assert gate.acquire(blocking=False) and gate.acquire(blocking=False)
        gate.release(), gate.release()


class TestMainCommandLogging:
    # MJDs before sdssdb coverage and without raw data: no database query.
    def _run(self, args, monkeypatch, caplog, env=None):
        from almanac.cli import main

        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        # Workers must not connect to a real database in tests.
        fake = types.ModuleType("almanac.database")
        fake.database = types.SimpleNamespace(close=lambda: None)
        fake.is_database_available = False
        monkeypatch.setitem(sys.modules, "almanac.database", fake)
        with caplog.at_level(logging.INFO):
            r = CliRunner().invoke(main, args, catch_exceptions=False)
        assert r.exit_code == 0
        return [m for m in caplog.messages if m.startswith("Parallelism:")]

    def test_one_night_runs_in_process(self, monkeypatch, caplog, tmp_path):
        lines = self._run(["--mjd", "51000", "--apo", "-O", str(tmp_path / "o.h5")],
                          monkeypatch, caplog, env={"SLURM_CPUS_ON_NODE": "8"})
        assert len(lines) == 1
        assert "processes=1" in lines[0] and "capped at 1 task" in lines[0]

    def test_slurm_default_with_gate(self, monkeypatch, caplog, tmp_path):
        lines = self._run(
            ["--mjd-start", "51000", "--mjd-end", "51003", "--apo", "--query-workers", "1",
             "-O", str(tmp_path / "o.h5")],
            monkeypatch, caplog, env={"SLURM_CPUS_ON_NODE": "2"},
        )
        assert "processes=2" in lines[0]
        assert "SLURM_CPUS_ON_NODE=2" in lines[0] or "affinity" in lines[0]
        assert "at most 1 worker(s) hold a database connection" in lines[0]

    def test_explicit_p12(self, monkeypatch, caplog, tmp_path):
        lines = self._run(
            ["-p", "12", "--mjd-start", "51000", "--mjd-end", "51001", "--apo",
             "-O", str(tmp_path / "o.h5")],
            monkeypatch, caplog,
        )
        assert "processes=2 (from --processes; capped at 2 task(s))" in lines[0]
