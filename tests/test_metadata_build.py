"""Resumable / incremental `almanac add metadata` (no database: fake query functions)."""

import hashlib
import json
import os
import stat

import h5py as h5
import numpy as np
import pytest
from click.testing import CliRunner
from peewee import OperationalError

import almanac.metadata as md
from almanac.cli import main

IDS = list(range(100, 160))  # 60 positive sdss_id
NOT_FOUND = {i for i in IDS if i % 7 == 0}  # ids the fake catalog does not know


def fake_row(sdss_id):
    return dict(
        sdss_id=sdss_id,
        catalogid=10_000 + sdss_id,
        version_id=31,
        lead="gaia_dr3_source",
        gaia_dr3_source=900_000 + sdss_id,
        gaia_ra=float(sdss_id) / 2,
        gaia_parallax=float(sdss_id) / 100,
        twomass_designation=f"J{sdss_id:08d}",
        carton_pks={999_999, 1_000_000 + sdss_id % 3},  # unknown to sdss_semaphore
    )


def fake_query(ids, suffix=""):
    return {i: fake_row(i) for i in ids if i not in NOT_FOUND}


class Recorder:
    """Fake query that records the ids it was asked for and can fail on demand."""

    def __init__(self, fail_on_batch_call=None, fail_exc=None, fail_times=1):
        self.calls = []
        self.fail_on_batch_call = fail_on_batch_call
        self.fail_exc = fail_exc
        self.fail_times = fail_times

    def __call__(self, ids, suffix=""):
        n = len(self.calls)
        self.calls.append(list(ids))
        if self.fail_on_batch_call is not None and n >= self.fail_on_batch_call and self.fail_times > 0:
            self.fail_times -= 1
            raise self.fail_exc
        return fake_query(ids, suffix)

    @property
    def queried(self):
        return sorted(i for c in self.calls for i in c)


def make_almanac(path, ids=IDS):
    """Minimal almanac file: two nights, fibers with sdss_id (incl. -1 and repeats)."""
    with h5.File(path, "w") as fp:
        half = len(ids) // 2
        g1 = fp.create_group("raw/apo/60000/fibers/1001")
        g1.create_dataset("sdss_id", data=np.array([-1, *ids[:half], ids[0]], dtype=np.int64))
        g2 = fp.create_group("raw/lco/60001/fibers/2002")
        g2.create_dataset("sdss_id", data=np.array([*ids[half:], -1], dtype=np.int64))
        fp.create_group("raw/apo/60000/exposures")
    return path


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read_cartons(path):
    with h5.File(path, "r") as fp:
        return fp["meta_carton_pks/sdss_id"][:], fp["meta_carton_pks/carton_pk"][:]


def read_meta(path):
    with h5.File(path, "r") as fp:
        g = fp["meta"]
        cols = {k: g[k][()] for k in g.keys()}
        attrs = dict(g.attrs)
        nf = fp["meta_not_found/sdss_id"][:]
    return cols, attrs, nf


def build(tmp_path, query, **kw):
    kw.setdefault("query_workers", 0)
    kw.setdefault("batch_size", 8)
    kw.setdefault("retry_backoff", 0.0)
    return md.build_metadata(query_batch=query, **kw)


def assert_same_meta(a, b):
    assert list(a.keys()) == list(b.keys())
    for k in a:
        np.testing.assert_array_equal(a[k], b[k], err_msg=k)


class TestOutputMode:
    def test_reads_input_read_only_and_writes_targets_file(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        before = sha(inp)
        os.chmod(inp, stat.S_IRUSR | stat.S_IRGRP)  # opening it for writing would fail
        out = str(tmp_path / "targets.h5")
        try:
            build(tmp_path, fake_query, input_path=inp, output_path=out)
        finally:
            os.chmod(inp, stat.S_IRUSR | stat.S_IWUSR)
        assert sha(inp) == before
        with h5.File(inp, "r") as fp:
            assert "meta" not in fp
        cols, attrs, nf = read_meta(out)
        expected = sorted(set(IDS) - NOT_FOUND)
        np.testing.assert_array_equal(cols["sdss_id"], expected)
        np.testing.assert_array_equal(nf, sorted(NOT_FOUND))
        np.testing.assert_allclose(cols["gaia_ra"], np.array(expected) / 2)
        assert cols["sdss5_target_flags"].shape[0] == len(expected)
        # no temporary file or checkpoint left behind
        assert sorted(os.listdir(tmp_path)) == ["almanac.h5", "targets.h5"]

    def test_provenance_attributes(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "targets.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=out)
        _, attrs, _ = read_meta(out)
        for key in ("almanac_version", "almanac_commit", "input_path", "n_sdss_id",
                    "database_host", "query_start_utc", "query_end_utc", "columns", "since_used"):
            assert key in attrs, key
        assert attrs["input_path"] == os.path.abspath(inp)
        assert attrs["n_sdss_id"] == len(set(IDS) - NOT_FOUND)
        assert attrs["n_sdss_id_input"] == len(IDS)
        assert not attrs["since_used"]
        assert "sdss_id" in [c.decode() if isinstance(c, bytes) else c for c in attrs["columns"]]
        for v in attrs.values():  # no credentials anywhere
            assert "password" not in str(v).lower()

    def test_output_equal_to_input_is_refused(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        with pytest.raises(md.MetadataBuildError):
            build(tmp_path, fake_query, input_path=inp, output_path=inp)

    def test_pool_matches_inline(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "a.h5"))
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "b.h5"), query_workers=2)
        assert_same_meta(read_meta(str(tmp_path / "a.h5"))[0], read_meta(str(tmp_path / "b.h5"))[0])


class TestResume:
    def test_resume_after_interrupted_batch(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "targets.h5")
        ckpt = str(tmp_path / "ckpt")

        # First run dies on its 4th batch (non-transient error, no retries left).
        first = Recorder(fail_on_batch_call=3, fail_exc=RuntimeError("boom"), fail_times=99)
        with pytest.raises(md.MetadataBuildError):
            build(tmp_path, first, input_path=inp, output_path=out,
                  checkpoint_dir=ckpt, max_batch_attempts=1)
        assert not os.path.exists(out)
        done = sorted(f for f in os.listdir(ckpt) if f.startswith("batch_") and f.endswith(".h5"))
        assert done == ["batch_000000.h5", "batch_000001.h5", "batch_000002.h5"]

        # Second run only queries the batches that were not finished.
        second = Recorder()
        build(tmp_path, second, input_path=inp, output_path=out, checkpoint_dir=ckpt)
        assert second.queried == sorted(set(IDS) - set(first.queried[: 3 * 8]))
        assert len(second.queried) == len(IDS) - 3 * 8

        # Identical to an uninterrupted build.
        ref = str(tmp_path / "ref.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=ref)
        assert_same_meta(read_meta(out)[0], read_meta(ref)[0])
        assert not os.path.exists(ckpt)  # removed after success by default

    def test_half_written_batch_file_is_redone(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "targets.h5")
        ckpt = str(tmp_path / "ckpt")
        first = Recorder(fail_on_batch_call=2, fail_exc=RuntimeError("boom"), fail_times=99)
        with pytest.raises(md.MetadataBuildError):
            build(tmp_path, first, input_path=inp, output_path=out,
                  checkpoint_dir=ckpt, max_batch_attempts=1)
        # A crash mid-write leaves only a temporary name; also corrupt a finished one.
        open(os.path.join(ckpt, "batch_000002.h5.tmp-1"), "wb").write(b"partial")
        open(os.path.join(ckpt, "batch_000001.h5"), "wb").write(b"garbage")
        second = Recorder()
        build(tmp_path, second, input_path=inp, output_path=out, checkpoint_dir=ckpt)
        assert len(second.queried) == len(IDS) - 8  # batch 0 kept; 1 (corrupt) and 2.. redone
        ref = str(tmp_path / "ref.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=ref)
        assert_same_meta(read_meta(out)[0], read_meta(ref)[0])

    def test_transient_database_error_is_retried(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "targets.h5")
        flaky = Recorder(fail_on_batch_call=2, fail_exc=OperationalError("server closed the connection"),
                         fail_times=2)
        build(tmp_path, flaky, input_path=inp, output_path=out, max_batch_attempts=3)
        cols, _, _ = read_meta(out)
        np.testing.assert_array_equal(cols["sdss_id"], sorted(set(IDS) - NOT_FOUND))

    def test_checkpoint_for_other_id_set_is_refused(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        ckpt = str(tmp_path / "ckpt")
        first = Recorder(fail_on_batch_call=1, fail_exc=RuntimeError("boom"), fail_times=99)
        with pytest.raises(md.MetadataBuildError):
            build(tmp_path, first, input_path=inp, output_path=str(tmp_path / "o.h5"),
                  checkpoint_dir=ckpt, max_batch_attempts=1)
        other = make_almanac(str(tmp_path / "other.h5"), ids=list(range(500, 530)))
        with pytest.raises(md.MetadataBuildError, match="different id set"):
            build(tmp_path, fake_query, input_path=other, output_path=str(tmp_path / "o.h5"),
                  checkpoint_dir=ckpt)
        build(tmp_path, fake_query, input_path=other, output_path=str(tmp_path / "o.h5"),
              checkpoint_dir=ckpt, fresh=True)
        np.testing.assert_array_equal(read_meta(str(tmp_path / "o.h5"))[0]["sdss_id"],
                                      [i for i in range(500, 530) if i not in NOT_FOUND])

    def test_progress_and_manifest(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        ckpt = str(tmp_path / "ckpt")
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "o.h5"),
              checkpoint_dir=ckpt, keep_checkpoint=True)
        progress = json.load(open(os.path.join(ckpt, "progress.json")))
        assert progress["ids_done"] == progress["ids_total"] == len(IDS)
        manifest = json.load(open(os.path.join(ckpt, "manifest.json")))
        assert manifest["query_start_utc"] and manifest["query_end_utc"]


class TestIncremental:
    def test_since_queries_only_new_ids(self, tmp_path):
        old_ids = IDS[:35]
        old = make_almanac(str(tmp_path / "old.h5"), ids=old_ids)
        build(tmp_path, fake_query, input_path=old, output_path=str(tmp_path / "t_old.h5"))

        new = make_almanac(str(tmp_path / "new.h5"), ids=IDS)
        rec = Recorder()
        build(tmp_path, rec, input_path=new, output_path=str(tmp_path / "t_new.h5"),
              since=str(tmp_path / "t_old.h5"))
        # not-found ids of the previous build are not re-queried by default
        assert rec.queried == sorted(set(IDS) - set(old_ids))

        ref = str(tmp_path / "ref.h5")
        build(tmp_path, fake_query, input_path=new, output_path=ref)
        cols, attrs, nf = read_meta(str(tmp_path / "t_new.h5"))
        assert_same_meta(cols, read_meta(ref)[0])
        np.testing.assert_array_equal(nf, sorted(NOT_FOUND))
        assert attrs["since_used"]
        assert attrs["n_copied_from_since"] == len(set(old_ids) - NOT_FOUND)
        assert attrs["n_queried"] == len(set(IDS) - set(old_ids))
        for a, b in zip(read_cartons(str(tmp_path / "t_new.h5")), read_cartons(ref)):
            np.testing.assert_array_equal(a, b)

    def test_since_retry_not_found(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_query, input_path=inp, output_path=str(tmp_path / "t1.h5"))
        rec = Recorder()
        build(tmp_path, rec, input_path=inp, output_path=str(tmp_path / "t2.h5"),
              since=str(tmp_path / "t1.h5"), retry_not_found=True)
        assert rec.queried == sorted(NOT_FOUND)

    def test_since_drops_rows_not_in_input_and_in_place_since_self(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_query, input_path=inp, restrict_to=None)  # in place, full
        small = make_almanac(str(tmp_path / "small.h5"), ids=IDS[:10])
        rec = Recorder()
        build(tmp_path, rec, input_path=small, since=inp)  # in place on small, copy from a.h5
        assert rec.calls == []
        cols, _, _ = read_meta(small)
        np.testing.assert_array_equal(cols["sdss_id"], [i for i in IDS[:10] if i not in NOT_FOUND])

    def test_restrict_to(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        keep = tmp_path / "keep.txt"
        keep.write_text("# ids\n" + "\n".join(str(i) for i in IDS[::2]) + "\n99999\n")
        rec = Recorder()
        out = str(tmp_path / "t.h5")
        build(tmp_path, rec, input_path=inp, output_path=out, restrict_to=str(keep))
        assert rec.queried == IDS[::2]
        # later: fill the rest incrementally
        rec2 = Recorder()
        build(tmp_path, rec2, input_path=inp, output_path=str(tmp_path / "t2.h5"), since=out)
        assert rec2.queried == IDS[1::2]


class TestCartons:
    def test_raw_carton_pairs_kept_even_when_semaphore_does_not_know_them(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        out = str(tmp_path / "t.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=out)
        sid, pk = read_cartons(out)
        found = sorted(set(IDS) - NOT_FOUND)
        exp = sorted((i, p) for i in found for p in (999_999, 1_000_000 + i % 3))
        assert list(zip(sid.tolist(), pk.tolist())) == exp
        _, attrs, _ = read_meta(out)
        assert 999_999 in attrs["unknown_carton_pks"]
        assert attrs["n_carton_pairs"] == len(exp)


class TestAtomicWrite:
    def test_in_place_failure_keeps_old_meta(self, tmp_path, monkeypatch):
        inp = make_almanac(str(tmp_path / "a.h5"))
        build(tmp_path, fake_query, input_path=inp)
        old_cols, _, _ = read_meta(inp)

        original = md._write_meta_group

        def broken(group, columns, *a, **k):
            group.create_dataset("sdss_id", data=columns["sdss_id"])  # half-written
            raise OSError("disk full")

        monkeypatch.setattr(md, "_write_meta_group", broken)
        with pytest.raises(OSError):
            build(tmp_path, fake_query, input_path=inp, fresh=True)
        monkeypatch.setattr(md, "_write_meta_group", original)

        cols, _, _ = read_meta(inp)
        assert_same_meta(cols, old_cols)
        with h5.File(inp, "r") as fp:
            assert md.META_TMP_GROUP not in fp
            assert "raw/apo/60000/fibers/1001/sdss_id" in fp  # rest of the file untouched

    def test_output_failure_leaves_no_partial_file(self, tmp_path, monkeypatch):
        inp = make_almanac(str(tmp_path / "a.h5"))
        out = str(tmp_path / "t.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=out)
        before = sha(out)

        def broken(group, columns, *a, **k):
            group.create_dataset("sdss_id", data=columns["sdss_id"])
            raise OSError("disk full")

        monkeypatch.setattr(md, "_write_meta_group", broken)
        with pytest.raises(OSError):
            build(tmp_path, fake_query, input_path=inp, output_path=out)
        assert sha(out) == before  # previous targets file untouched
        assert not [f for f in os.listdir(tmp_path) if ".tmp-" in f]

    def test_in_place_replaces_meta_and_keeps_other_groups(self, tmp_path):
        inp = make_almanac(str(tmp_path / "a.h5"))
        with h5.File(inp, "a") as fp:
            fp.create_group("meta").create_dataset("stale", data=[1, 2, 3])
            fp.create_dataset("exposure_class", data=[7])
        build(tmp_path, fake_query, input_path=inp)
        with h5.File(inp, "r") as fp:
            assert "stale" not in fp["meta"]
            assert fp["exposure_class"][0] == 7
            assert len(fp["meta/sdss_id"]) == len(set(IDS) - NOT_FOUND)


class TestCli:
    def test_cli_output_since_and_checkpoint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "_default_query_batch", fake_query)
        inp = make_almanac(str(tmp_path / "a.h5"))
        out = str(tmp_path / "t.h5")
        runner = CliRunner()
        r = runner.invoke(main, ["add", "metadata", inp, "--output", out, "--query-workers", "0",
                                 "--batch-size", "16", "--keep-checkpoint"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert os.path.isdir(out + ".meta-checkpoint")
        r = runner.invoke(main, ["add", "metadata", inp, "--output", str(tmp_path / "t2.h5"),
                                 "--since", out, "--query-workers", "0"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        assert_same_meta(read_meta(out)[0], read_meta(str(tmp_path / "t2.h5"))[0])

    def test_cli_help_documents_copy_workflow(self):
        r = CliRunner().invoke(main, ["add", "metadata", "--help"])
        assert "COPY" in r.output and "--since" in r.output and "--output" in r.output


class TestSolarSystemRow:
    """sdss_id 0 (a solar-system body placed on a fiber by an override): a fixed row, never queried."""

    def test_fixed_row_written_and_not_queried(self, tmp_path):
        inp = str(tmp_path / "almanac.h5")
        make_almanac(inp)
        with h5.File(inp, "a") as fp:
            g = fp.create_group("raw/apo/55755/fibers/1005755001")
            g.create_dataset("sdss_id", data=np.array([-1, 0, IDS[0]], dtype=np.int64))
        out = str(tmp_path / "targets.h5")
        rec = Recorder()
        build(tmp_path, rec, input_path=inp, output_path=out)
        assert 0 not in rec.queried and -1 not in rec.queried
        cols, attrs, nf = read_meta(out)
        assert cols["sdss_id"][0] == 0 and 0 not in nf
        assert cols["lead"][0].decode() == "solar_system"
        assert cols["catalogid"][0] == -1 and cols["version_id"][0] == -1
        assert np.isnan(cols["gaia_ra"][0]) and np.isnan(cols["twomass_h_m"][0])
        assert not cols["sdss5_target_flags"][0].any()
        assert attrs["n_solar_system_rows"] == 1
        assert attrs["n_sdss_id"] == len(set(IDS) - NOT_FOUND) + 1

    def test_no_row_without_id_zero(self, tmp_path):
        inp = make_almanac(str(tmp_path / "almanac.h5"))
        out = str(tmp_path / "targets.h5")
        build(tmp_path, fake_query, input_path=inp, output_path=out)
        cols, attrs, _ = read_meta(out)
        assert 0 not in cols["sdss_id"] and attrs["n_solar_system_rows"] == 0
